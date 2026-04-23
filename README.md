# iPhone WDA USB Control Starter

This is a small Python starter app for controlling your own trusted iPhone over USB with XCUITest WebDriverAgent (WDA). It does not run an Appium server at command time; it sends HTTP commands directly to WDA through a local USB port-forward.

The moving parts are:

- Xcode builds and signs `WebDriverAgentRunner`.
- Your iPhone runs `WebDriverAgentRunner`, which exposes WDA on port `8100`.
- `iproxy` forwards `localhost:8100` on your Mac to the iPhone over USB.
- `phonectl` or your Python code sends WDA commands.

## 1. Install Local Tools

You already have Xcode. This workspace saw `xcodebuild`, `node`, and `npm`, but not `appium` or `iproxy`.

```bash
npm install -g appium
appium driver install xcuitest
```

Install `iproxy` with one of these:

```bash
brew install libimobiledevice
```

or:

```bash
npm install -g iproxy
```

Check your machine:

```bash
python3 -m pip install -e .
phonectl doctor
```

## 2. Prepare The iPhone

On the iPhone:

1. Plug it in over USB and tap **Trust This Computer**.
2. Enable **Developer Mode** if iOS prompts you to do so.
3. Keep the device unlocked while you are getting WDA running.

In Xcode:

1. Open WDA:

   ```bash
   appium driver run xcuitest open-wda
   ```

   If that command is unavailable after installing Appium, use:

   ```bash
   scripts/open_wda.sh
   ```

2. Select your physical iPhone as the run destination.
3. Select the `WebDriverAgentRunner` scheme.
4. In Signing & Capabilities, choose your Apple developer team for `WebDriverAgentRunner`.
5. Change the WDA runner bundle id if Xcode says the default one is unavailable. A common pattern is `com.yourname.WebDriverAgentRunner`.
6. Run Test with `Cmd-U`. The phone should show an automation overlay, and Xcode logs should say WDA is listening.

## 3. Try Direct USB Commands

Once WDA is running on the phone:

```bash
phonectl status
phonectl session
phonectl size
phonectl screenshot screen.png
phonectl tap 100 200
phonectl swipe up
phonectl drag 200 700 200 200 --duration 0.6
phonectl source --format json
```

To start WDA again later without opening Xcode's UI:

```bash
scripts/start_wda.sh
```

Leave that terminal open while using the phone. In another terminal:

```bash
phonectl --no-iproxy status
phonectl --no-iproxy tap 100 200
```

If multiple iPhones are connected:

```bash
scripts/start_wda.sh --udid YOUR_DEVICE_UDID
```

For multiple USB devices, pass the UDID:

```bash
phonectl --udid YOUR_DEVICE_UDID status
```

If you already started `iproxy` yourself, skip the built-in port-forward:

```bash
iproxy 8100 8100
phonectl --no-iproxy status
```

## 4. Control Apps

Create a session attached to SpringBoard/current foreground app:

```bash
phonectl session
```

Launch Safari:

```bash
phonectl launch com.apple.mobilesafari
```

Open a URL:

```bash
phonectl open-url https://example.com --bundle-id com.apple.mobilesafari
```

Find and click by accessibility id:

```bash
phonectl click --using "accessibility id" --value "Continue"
```

Type into the focused element:

```bash
phonectl type "hello from python"
```

## Appium Mode

If you want Appium to start/manage WDA, run the Appium server in one terminal:

```bash
scripts/start_appium.sh
```

Then run the Python Appium example in another terminal:

```bash
UDID=YOUR_DEVICE_UDID python3 examples/appium_control.py
```

For this phone, the detected UDID was:

```bash
UDID=00008140-001A50A93630401C python3 examples/appium_control.py
```

If you changed WDA's bundle id in Xcode, pass it too:

```bash
WDA_BUNDLE_ID=com.yourname.WebDriverAgentRunner UDID=YOUR_DEVICE_UDID python3 examples/appium_control.py
```

If you refresh the packaged runner app's provisioning profile, embed and re-sign
it with:

```bash
scripts/update_wda_provision.sh
```

## 5. Use From Python

```python
from iphone_wda_usb import IProxy, WDAClient

with IProxy(local_port=8100, remote_port=8100):
    phone = WDAClient("http://127.0.0.1:8100")
    print(phone.wait_until_ready())
    phone.create_session()
    phone.tap(100, 200)
    phone.save_screenshot("screen.png")
```

Run the included example:

```bash
python3 examples/basic_control.py
```

## Troubleshooting

- `iproxy` missing: install `libimobiledevice` with Homebrew or `iproxy` with NPM.
- WDA `status` fails: make sure `WebDriverAgentRunner` is currently running from Xcode and the phone is plugged in and trusted.
- Xcode signing fails: use your personal/team developer account, change the WDA runner bundle id, and trust the developer certificate on the iPhone if iOS asks.
- Commands hang or fail after the screen locks: unlock the iPhone and rerun WDA.
- Multiple devices attached: pass `--udid`.

## Useful References

- Appium XCUITest real device configuration: https://appium.github.io/appium-xcuitest-driver/latest/guides/real-device-config/
- Appium XCUITest driver repo: https://github.com/appium/appium-xcuitest-driver
- Appium WebDriverAgent repo: https://github.com/appium/WebDriverAgent
- libusbmuxd / iproxy: https://github.com/libimobiledevice/libusbmuxd
