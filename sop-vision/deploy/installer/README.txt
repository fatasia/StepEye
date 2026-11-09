StepEye 0.9.0 Windows x64 安装程序

安装位置：%LOCALAPPDATA%\StepEye
安装内容：便携运行包（服务 + 前端 + 文档 + 演示模型 + Monibuca v6 beta）
卸载：设置 → 应用 → StepEye，或安装目录的 uninstall.exe。卸载保留 data\ 目录。

启动：
  1. 开始菜单打开 StepEye，自动启动演示工位 http://127.0.0.1:8000
  2. 需要登录时，在安装目录打开终端：
     stepeye-runtime.exe passwd admin --role admin --data data
     stepeye-runtime.exe serve .\_internal\configs\demo_sim.yaml --port 8000 --auth --data data

升级：运行新版安装程序，会替换程序文件并保留 data\ 目录。
