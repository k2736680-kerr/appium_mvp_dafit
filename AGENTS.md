# Android 运行环境隔离规则

1. 本项目 Appium 默认且专用端口为 `4725`，禁止复用其他项目的 `4723`。
2. 宿主机 ADB daemon（默认 5037）由所有实体机和模拟器共享。任何运行、诊断和恢复流程均禁止执行 `adb kill-server`，也禁止终止全部 `adb.exe`。
3. 模拟器恢复只能操作 `ANDROID_ADB_SERIAL` 指定的目标设备，禁止按 `emulator.exe`、`qemu-system-*` 镜像名批量终止进程。
4. Appium 恢复只能操作本项目配置的专用端口，并且进程命令行必须匹配专属 `/appium-mvp` Base Path；不得终止归属无法确认的进程。
