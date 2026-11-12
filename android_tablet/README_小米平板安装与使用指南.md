# ⚡ TradeRadar 美股量化雷达 · 小米平板极速控制中心指南

本程序是专门为 **小米平板 / 安卓设备** 定制开发的超轻量版量化盯盘与控制台系统：
- **纯 Python 原生标准库**：零编译、零庞大 C++ 依赖（不用编译报错的 LightGBM/Scipy），在平板上开箱即跑！
- **1:1 复刻电脑版控制中心暗黑 UI**：大触控按键、实时美东时段检测、黑底绿字滚动控制台日志。
- **支持「添加到主屏幕」**：在平板浏览器打开后，一键生成桌面图标，点开就是全屏 App！
- **双通道秒级预警**：支持微信（ClawBot + 公众号）与 Discord Webhook 实时价格报警。

---

## 🚀 三步极速上手指南

### 第一步：在小米平板安装 Termux 终端
> Termux 是安卓最成熟、免 Root 的极简 Linux 终端。
1. 在小米应用商店、浏览器或 F-Droid 搜索下载 **Termux**（建议直接去 [F-Droid 官方页面下载](https://f-droid.org/zh_Hans/packages/com.termux/) 最稳定）。
2. 打开 Termux，首次启动只需执行一条命令安装 Python：
   ```bash
   pkg install python -y
   ```

---

### 第二步：将本文件夹传到平板并运行
1. 将电脑桌面上的 `TradeRadar_Android_Tablet` 整个文件夹通过 **微信 / QQ / USB数据线** 传到小米平板（例如存放在平板的 `Download` 文件夹中）。
2. 在 Termux 中运行以下命令授权访问存储并启动：
   ```bash
   # 授权存储权限 (弹窗点击“允许”)
   termux-setup-storage

   # 切换到解压后的文件夹 (以 Download 为例)
   cd /sdcard/Download/TradeRadar_Android_Tablet

   # 启动轻量控制中心
   python TradeRadar_Android.py
   ```
3. 终端会打印出本地访问地址：`http://127.0.0.1:8765`

---

### 第三步：在平板浏览器中一键生成全屏 App
1. 打开小米平板自带的 **系统浏览器** 或 **Chrome**。
2. 在地址栏输入：
   👉 **`http://127.0.0.1:8765`**
3. 此时屏幕上就会出现与电脑版一模一样的暗黑控制台！
4. 点击浏览器右上角菜单 $\rightarrow$ 选择 **「添加到主屏幕」**（Add to Home Screen）：
   - 平板桌面上会立即生成一个 **⚡ 雷达控制台** 的专属图标；
   - 以后直接在平板桌面点开，就是**无边框的原生全屏 App**！

---

### 🛡️ 小米系统（HyperOS / MIUI）长久保活小技巧
为了让平板在熄屏后依然能够 24 小时 60 秒持续稳定轮询，只需做两项设置：
1. **防止 CPU 睡眠**：在 Termux 终端里输入一次 `termux-wake-lock`（脚本已内置自动调用）。
2. **小米省电无限制**：
   - 打开平板 **「设置」** $\rightarrow$ **「应用设置」** $\rightarrow$ **「应用管理」** $\rightarrow$ 找到 **Termux**；
   - **省电策略**：选择 **「无限制」**；
   - **自启动权限**：设置为 **「允许自启动」**。
