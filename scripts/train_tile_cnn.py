#!/usr/bin/env python3
"""Train a small tile-letter CNN from tile_dataset/train and tile_dataset/val."""

from __future__ import annotations

import random
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler


DATASET_PATH = Path("tile_dataset")
MODEL_PATH = Path("models/tile_cnn.pt")
IMAGE_SIZE = 32
BATCH_SIZE = 64
EPOCHS = 60
LEARNING_RATE = 0.001
SEED = 7
BALANCED_SAMPLES_PER_CLASS = 120
MIN_TRAIN_SAMPLES_PER_CLASS = 2
IGNORED_CLASSES: set[str] = set()


class TileDataset(Dataset):
    def __init__(self, split: str, classes: list[str], augment: bool = False) -> None:
        self.augment = augment
        self.classes = classes
        self.samples: list[tuple[Path, int]] = []
        for index, label in enumerate(classes):
            for path in sorted((DATASET_PATH / split / label).glob("*.png")):
                self.samples.append((path, index))

    def __len__(self) -> int:
        return len(self.samples)

    def class_counts(self) -> list[int]:
        counts = [0 for _ in self.classes]
        for _, label in self.samples:
            counts[label] += 1
        return counts

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        path, label = self.samples[index]
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise RuntimeError(f"Could not read {path}")
        image = cv2.resize(image, (IMAGE_SIZE, IMAGE_SIZE), interpolation=cv2.INTER_AREA)
        if self.augment:
            image = augment_image(image)
        tensor = torch.from_numpy(image).float().unsqueeze(0) / 255.0
        return tensor, torch.tensor(label, dtype=torch.long)


class TileCNN(nn.Module):
    def __init__(self, class_count: int) -> None:
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 8, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(8, 16, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(16, 32, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d((1, 1)),
        )
        self.classifier = nn.Linear(32, class_count)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.features(x)
        return self.classifier(x.flatten(1))


def main() -> int:
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)

    classes = discover_classes()
    if len(classes) < 2:
        raise RuntimeError("Need at least two labeled tile classes in tile_dataset/train")

    train_dataset = TileDataset("train", classes, augment=True)
    val_dataset = TileDataset("val", classes)
    if len(train_dataset) == 0:
        raise RuntimeError("No training images found")

    warn_about_small_classes(classes, train_dataset.class_counts(), val_dataset.class_counts())
    train_loader = DataLoader(
        train_dataset,
        batch_size=BATCH_SIZE,
        sampler=balanced_sampler(train_dataset),
    )
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE)
    model = TileCNN(len(classes))
    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE)
    loss_fn = nn.CrossEntropyLoss()

    for epoch in range(1, EPOCHS + 1):
        train_loss, train_acc = run_epoch(model, train_loader, loss_fn, optimizer)
        val_loss, val_acc = evaluate(model, val_loader, loss_fn) if len(val_dataset) else (0.0, 0.0)
        print(
            f"epoch {epoch:02d}: "
            f"train_loss={train_loss:.4f} train_acc={train_acc:.3f} "
            f"val_loss={val_loss:.4f} val_acc={val_acc:.3f}"
        )

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model_state": model.state_dict(),
            "classes": classes,
            "image_size": IMAGE_SIZE,
            "channels": 1,
        },
        MODEL_PATH,
    )
    print(f"saved {MODEL_PATH}")
    return 0


def discover_classes() -> list[str]:
    train_root = DATASET_PATH / "train"
    if not train_root.exists():
        return []
    return sorted(
        path.name
        for path in train_root.iterdir()
        if path.is_dir() and path.name not in IGNORED_CLASSES and list(path.glob("*.png"))
    )


def warn_about_small_classes(classes: list[str], train_counts: list[int], val_counts: list[int]) -> None:
    print("class counts:")
    for label, train_count, val_count in zip(classes, train_counts, val_counts):
        marker = " *low*" if train_count < MIN_TRAIN_SAMPLES_PER_CLASS or val_count == 0 else ""
        print(f"  {label}: train={train_count} val={val_count}{marker}")


def balanced_sampler(dataset: TileDataset) -> WeightedRandomSampler:
    counts = dataset.class_counts()
    weights = [1.0 / max(counts[label], 1) for _, label in dataset.samples]
    sample_count = max(len(dataset.samples), len(counts) * BALANCED_SAMPLES_PER_CLASS)
    return WeightedRandomSampler(weights, num_samples=sample_count, replacement=True)


def augment_image(image: np.ndarray) -> np.ndarray:
    if random.random() < 0.65:
        scale = random.uniform(0.86, 1.14)
        tx = random.uniform(-2.5, 2.5)
        ty = random.uniform(-2.5, 2.5)
        matrix = cv2.getRotationMatrix2D((IMAGE_SIZE / 2, IMAGE_SIZE / 2), 0, scale)
        matrix[0, 2] += tx
        matrix[1, 2] += ty
        image = cv2.warpAffine(image, matrix, (IMAGE_SIZE, IMAGE_SIZE), flags=cv2.INTER_NEAREST, borderValue=0)
    if random.random() < 0.35:
        kernel = np.ones((2, 2), dtype=np.uint8)
        if random.random() < 0.5:
            image = cv2.dilate(image, kernel, iterations=1)
        else:
            image = cv2.erode(image, kernel, iterations=1)
    if random.random() < 0.15:
        noise = np.random.normal(0, 6, image.shape).astype(np.int16)
        image = np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return image


def run_epoch(
    model: TileCNN,
    loader: DataLoader,
    loss_fn: nn.Module,
    optimizer: torch.optim.Optimizer,
) -> tuple[float, float]:
    model.train()
    total_loss = 0.0
    correct = 0
    total = 0
    for images, labels in loader:
        optimizer.zero_grad()
        logits = model(images)
        loss = loss_fn(logits, labels)
        loss.backward()
        optimizer.step()
        total_loss += float(loss.item()) * labels.size(0)
        correct += int((logits.argmax(dim=1) == labels).sum().item())
        total += labels.size(0)
    return total_loss / max(total, 1), correct / max(total, 1)


def evaluate(model: TileCNN, loader: DataLoader, loss_fn: nn.Module) -> tuple[float, float]:
    model.eval()
    total_loss = 0.0
    correct = 0
    total = 0
    with torch.no_grad():
        for images, labels in loader:
            logits = model(images)
            loss = loss_fn(logits, labels)
            total_loss += float(loss.item()) * labels.size(0)
            correct += int((logits.argmax(dim=1) == labels).sum().item())
            total += labels.size(0)
    return total_loss / max(total, 1), correct / max(total, 1)


if __name__ == "__main__":
    raise SystemExit(main())
