from iphone_wda_usb import IProxy, WDAClient


def main() -> None:
    with IProxy(local_port=8100, remote_port=8100):
        phone = WDAClient("http://127.0.0.1:8100")
        print(phone.wait_until_ready())
        phone.create_session()
        print(phone.window_size())
        phone.tap(100, 100)
        phone.save_screenshot("screen.png")


if __name__ == "__main__":
    main()
