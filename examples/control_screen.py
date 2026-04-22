import time

from iphone_wda_usb import WDAClient


def main() -> None:
    phone = WDAClient("http://127.0.0.1:8100")

    print(phone.wait_until_ready())
    phone.create_session()

    size = phone.window_size()
    width = size["width"]
    height = size["height"]
    print(f"Screen size: {width} x {height}")

    phone.save_screenshot("before.png")

    # Tap near the middle of the screen.
    phone.tap(width / 2, height / 2)
    time.sleep(0.5)

    # Swipe up, similar to dragging content upward with your finger.
    phone.swipe("up")
    time.sleep(0.5)

    phone.save_screenshot("after.png")


if __name__ == "__main__":
    main()
