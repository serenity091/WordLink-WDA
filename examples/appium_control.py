import os
import time

from appium import webdriver
from appium.options.ios import XCUITestOptions
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput


APPIUM_SERVER = os.environ.get("APPIUM_SERVER", "http://127.0.0.1:4723")
UDID = os.environ.get("UDID", "00008140-001A50A93630401C")
DEVICE_NAME = os.environ.get("DEVICE_NAME", "iPhone")
WDA_BUNDLE_ID = os.environ.get("WDA_BUNDLE_ID", "com.facebook.WebDriverAgentRunner")


def make_driver(bundle_id: str | None = None) -> webdriver.Remote:
    options = XCUITestOptions()
    options.platform_name = "iOS"
    options.automation_name = "XCUITest"
    options.udid = UDID
    options.device_name = DEVICE_NAME
    options.no_reset = True
    options.set_capability("appium:wdaLocalPort", 8100)
    options.set_capability("appium:updatedWDABundleId", WDA_BUNDLE_ID)

    if bundle_id:
        options.bundle_id = bundle_id

    return webdriver.Remote(APPIUM_SERVER, options=options)


def tap(driver: webdriver.Remote, x: int, y: int) -> None:
    finger = PointerInput("touch", "finger")
    actions = ActionBuilder(driver, mouse=finger)
    actions.pointer_action.move_to_location(x, y)
    actions.pointer_action.pointer_down()
    actions.pointer_action.pause(0.05)
    actions.pointer_action.pointer_up()
    actions.perform()


def drag(driver: webdriver.Remote, x1: int, y1: int, x2: int, y2: int, duration_seconds: float = 0.5) -> None:
    finger = PointerInput("touch", "finger")
    actions = ActionBuilder(driver, mouse=finger)
    actions.pointer_action.move_to_location(x1, y1)
    actions.pointer_action.pointer_down()
    actions.pointer_action.pause(0.1)
    actions.pointer_action.move_to_location(x2, y2, duration=int(duration_seconds * 1000))
    actions.pointer_action.pointer_up()
    actions.perform()


def main() -> None:
    driver = make_driver()

    try:
        size = driver.get_window_size()
        print(f"Screen size: {size}")

        driver.save_screenshot("appium-before.png")

        tap(driver, size["width"] // 2, size["height"] // 2)
        time.sleep(0.5)

        drag(
            driver,
            size["width"] // 2,
            int(size["height"] * 0.75),
            size["width"] // 2,
            int(size["height"] * 0.25),
        )
        time.sleep(0.5)

        driver.save_screenshot("appium-after.png")

        # Example element lookup. Change the value to match what is on screen.
        # element = driver.find_element(AppiumBy.ACCESSIBILITY_ID, "Continue")
        # element.click()
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
