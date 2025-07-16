# 网易云音乐多功能下载中心 安装指南（零基础版）

本项目支持在 Windows 和 Linux 系统下零基础快速搭建，适合普通用户和开发者。

---

## 一、环境要求

- 操作系统：Windows 10/11 或 Linux（如 Ubuntu 20.04+/CentOS 7+/Debian 等）
- Python 3.7 及以上（推荐 3.8/3.9/3.10）
- 建议有 Chrome/Edge/Firefox 等现代浏览器

---

## 二、安装步骤

### 1. 安装 Python

#### Windows：
1. 访问 [Python 官网](https://www.python.org/downloads/windows/) 下载最新版 Python 3。
2. 安装时务必勾选 “Add Python to PATH” 选项。
3. 安装完成后，按 `Win + R` 输入 `cmd`，在命令行输入：
   ```
   python --version
   ```
   能看到版本号即安装成功。

#### Linux（以 Ubuntu 为例）：
1. 打开终端，输入：
   ```bash
   sudo apt update
   sudo apt install python3 python3-pip -y
   python3 --version
   pip3 --version
   ```
   能看到版本号即安装成功。

---

### 2. 安装依赖库

#### Windows：
1. 打开命令行（Win+R 输入 `cmd` 回车），切换到本项目文件夹，例如：
   ```
   cd F:\Test\Music
   ```
2. 安装依赖：
   ```
   pip install flask requests
   ```

#### Linux：
1. 打开终端，切换到项目目录，例如：
   ```bash
   cd /home/youruser/yourprojectdir
   ```
2. 安装依赖：
   ```bash
   pip3 install flask requests
   ```

---

### 3. 启动程序

#### Windows：
1. 在命令行输入：
   ```
   python web_downloader.py
   ```
2. 出现如下内容说明启动成功：
   ```
   * Running on http://127.0.0.1:5000/ (Press CTRL+C to quit)
   ```
3. 打开浏览器，访问：
   - 主页面： [http://127.0.0.1:5000/](http://127.0.0.1:5000/)
   - API文档： [http://127.0.0.1:5000/API_Document.html](http://127.0.0.1:5000/API_Document.html)

#### Linux：
1. 在终端输入：
   ```bash
   python3 web_downloader.py
   ```
2. 出现如下内容说明启动成功：
   ```
   * Running on http://127.0.0.1:5000/ (Press CTRL+C to quit)
   ```
3. 在本机或服务器浏览器访问：
   - 主页面： [http://服务器IP:5000/](http://服务器IP:5000/)
   - API文档： [http://服务器IP:5000/API_Document.html](http://服务器IP:5000/API_Document.html)

> **如需公网访问，请开放服务器5000端口，并确保安全组/防火墙允许外部访问。**

> **如需后台运行，推荐使用 `nohup` 或 `screen` 工具：**
> ```bash
> nohup python3 web_downloader.py &
> ```

---

## 三、常见问题

- **Q: 启动时报错“pip 不是内部或外部命令”？**
  - A: 说明 Python 没加到环境变量，重装 Python 并勾选“Add Python to PATH”。
- **Q: 端口被占用/打不开？**
  - A: 检查是否有其它程序占用 5000 端口，或尝试重启电脑/服务器。
- **Q: 下载慢/失败？**
  - A: 可能是网络问题，建议科学上网或多试几次。
- **Q: 网页打不开？**
  - A: 请确认命令行窗口有“Running on http://127.0.0.1:5000/”字样，或服务器端口已开放。

---

## 四、卸载与清理

- 关闭命令行/终端窗口即可停止服务。
- 删除本项目文件夹即可卸载。

---

## 五、进阶使用

- 支持扫码登录、批量下载、API接口调用等高级功能，详见 [API_Document.html](http://127.0.0.1:5000/API_Document.html) 或 [http://服务器IP:5000/API_Document.html](http://服务器IP:5000/API_Document.html)
- 如需自定义配置，可编辑 `config.json` 文件。

---

如有问题可在本页面或命令行窗口截图，向开发者反馈。 
