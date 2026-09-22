# macPad

Your touch Mac in iPad.

Run macOS GUI applications in your jailbroken iPad/iPhone. 

Features:

* Touch Gestures:
    - Scroll
    - hold to right click
    - Drag to resize/move window and move files
    - Drag to select text
    - Double tap to maximize window
    - Two fingers zoom and rotate
    - Two fingers tap to right click
    - Two fingers double tap zoom view
    - Two fingers hold and drag to share files to iOS
    - Three fingers mission control, swipe desktops and App Expose 
* Share files and clipboard between iOS and macPad
* Touch screen as trackpad
* Native CPU and GPU driver without any performance lost
    - Geekbench 6 CPU 2293 single-core score, 8156 multi-score score in my iPad Pro m1
    - Geekbench 6 GPU 32359 score in my iPad Pro m1
* Hardware video encode/decode
* Virtual keyboard
* Display density (125% and 150%)
* Native WiFi/Bluetooth/Apple pencil/Screen mirror, etc. supports
* Window Mode:
    - Run macOS applications like iOS native windows
    - Auto resized window
    - Auto popup new window
    - Fixed size window
* Fullscreen Mode:
    - Run macOS desktop in fullscreen

## Note

* Only test on iPad Pro m1 11-inch, iPadOS 16.3, dopamine jailbroken, macOS 13.4
* In theory, support all m-series and A18 jailbroken iPad/iPhone. But there are many hardcoded logics for my device's OS version currently
* Compared with [VirtualMacOniPad](https://github.com/nfzerox/VirtualMacOniPad), macPad uses native CPU and GPU drivers through chroot while VirtualBuddy uses hardware hypervirsor for CPU and mental2vulkan GPU  translation ([reims-vgpu](https://github.com/steelbrain/reims-vgpu)) which loss some performances
* Compared with [MacWSBootingGuide](https://github.com/khanhduytran0/MacWSBootingGuide), it uses MTLSimDriver for GPU which has many graphic issues and performance problems while macPad uses native GPU drivers

## Credits
- Codex
- [MacWSBootingGuide](https://github.com/khanhduytran0/MacWSBootingGuide)
- [AsahiLinux](https://github.com/AsahiLinux/linux)
- [iOS-run-macOS-executables-tools](https://github.com/zhuowei/iOS-run-macOS-executables-tools)
