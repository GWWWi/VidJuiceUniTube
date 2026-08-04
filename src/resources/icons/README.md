# 图标资源说明

本目录存放应用图标与界面用小图标。

## 必需文件

| 文件 | 用途 | 规格建议 |
|---|---|---|
| `app.ico` | 应用主图标（窗口图标 / 任务栏 / PyInstaller `--icon`） | 多尺寸 ICO：16 / 32 / 48 / 64 / 128 / 256 px |

> 仓库默认**不包含**二进制图标文件。首次打包前请自行放置 `app.ico`。
> 若文件缺失：
> - 运行时程序会跳过设置窗口图标（不影响功能）；
> - `build.spec` 会自动降级为不带图标打包。

## 生成方式

任选其一：

```bash
# 方式 A：用 Pillow 从 PNG 生成多尺寸 ICO
python -c "from PIL import Image; im=Image.open('logo.png'); im.save('app.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"

# 方式 B：使用 ImageMagick
magick convert logo.png -define icon:auto-resize=256,128,64,48,32,16 app.ico
```

## 图标来源建议

- 自行设计（推荐，避免授权风险）
- [Lucide](https://lucide.dev/)（ISC License）
- [Material Symbols](https://fonts.google.com/icons)（Apache-2.0）
- [Feather Icons](https://feathericons.com/)（MIT）

使用第三方图标时，请确认其许可协议允许商用与再分发，并在此处登记来源。

## 授权登记

| 文件 | 来源 | 许可协议 |
|---|---|---|
| `app.ico` | （待填写） | （待填写） |
