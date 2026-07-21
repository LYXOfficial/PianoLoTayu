# PianoLoTayu 

特别感谢 Deepseek V4 / Grok 4.5 / Kimi K2.6 / GPT 5.5 / Claude Code 的鼎力相助！（

在看一些特异视频的时候，经常看到有人把各种神秘歌曲转成钢琴唱歌的形式，乍一听是一坨噪音，不过如果听过原曲就能脑补出人声，不过感觉转换和瀑布预览都没有什么好用的工具，所以自己写了一个（

---

## Features

- **音频转 MIDI**：通过 STFT 频谱分析，根据转换参数，自动识别音频中的音高并生成标准 `.mid` 文件
- **可视化钢琴卷帘预览**：转换完成后可直接在钢琴卷帘预览生成的 MIDI，支持播放、缩放、轨道开关与音色控制，也可选择横向卷帘/纵向瀑布的模式
- **音视频导出**：多种格式导入，并基于 SoundFont 将 MIDI 渲染为 MP3 / M4A / OGG / AAC / FLAC / WAV 等格式，通过与预览相同的卷帘界面导出为 MP4 / MKV / WEBM 格式，提供详细的编码与码率选项（视频 音频 MP3/AAC/FLAC/PCM/Vorbis/Opus）
- **跨平台**：支持 Windows（x64 / ARM64转译）和 Linux（x64 / ARM64），若有条件可能支持 macOS

---

## Usage

### 方式一：下载预编译版本（仅GUI）

在 [Releases](https://github.com/LYXOfficial/PianoLoTayu/releases) 页面下载对应系统的压缩包：

- **Windows**：解压后运行 `pianolotayu-gui.exe`
- **Linux**：解压后运行 `pianolotayu-gui`

#### Linux 依赖

确保系统包含`FluidSynth`/`PulseAudio/PipeWire`/`FFmpeg`，若使用 GUI 版请在支持 `Qt6` 的桌面环境运行

可使用以下方式安装：

Debian / Ubuntu:

```sh
sudo apt update
sudo apt instal fluidsynth ffmpeg
``` 💡 预编译版本为 GUI 程序，无需安装 Python 环境。

Arch Linux:

```sh
sudo pacman -S fluidsynth ffmpeg
```

Windows 已预置依赖，无需下载

### 方式二：从源码运行

需要 Python ≥ 3.13，并使用 [uv](https://docs.astral.sh/uv/) 管理依赖：

```bash
pip install uv # Windows

curl -LsSf https://astral.sh/uv/install.sh | sh # Debian/Ubuntu

sudo pacman -S uv # Arch Linux
```

```bash
git clone https://github.com/LYXOfficial/PianoLoTayu.git
cd PianoLoTayu
uv sync

uv run python -m pianolotayu
uv run python -m pianolotayu.cli song.mp3 -o song.mid
```

---

## SoundFonts

PianoLoTayu 的音频预览和导出功能需要 **SoundFont（.sf2）** 文件来提供音色。项目本身不自带 SoundFont，需自行准备

本项目提供了一个存放 SoundFont 的分支，其中含有一个还原效果较纯粹且音域完整的正弦波，可以直接下载：

```bash
# 切换到 soundfonts 分支下载
git checkout soundfonts
# 将 .sf2 文件复制到主分支的 soundfonts/ 目录下
git checkout master
```

或者手动将 `.sf2` 文件放入以下任一位置：

- **开发环境**：项目根目录下的 `soundfonts/` 文件夹
- **预编译版本**：与可执行文件同目录的 `soundfonts/` 文件夹

> 🎵 你也可以使用自己收集的其他 `.sf2` 音色库文件。

---

## Wiki

### GUI 主界面

1. 将音频文件拖入窗口，或点击选择文件
2. 根据需要调整转换参数（采样率、FFT 窗口、阈值等）
3. 点击「开始转换」生成 MIDI
4. 转换完成后点击「钢琴卷帘预览」查看结果
5. 在预览窗口中可播放、导出音频或视频

### CLI 命令行

```bash
# 基础用法：将音频转换为 MIDI
uv run python -m pianolotayu.cli song.mp3

# 指定输出路径
uv run python -m pianolotayu.cli song.wav -o output.mid

# 调整参数
uv run python -m pianolotayu.cli song.mp3 \
    --threshold 25 \       # 峰值检测阈值，越低检测到的音符越多
    --max-notes 12 \        # 每帧最多同时音符数
    --min-duration 80 \     # 最短音符时长（毫秒）
    --dynamic-range 50      # 力度映射动态范围（dB）
```

### 参数说明

| CLI 参数 | 默认值 | 说明 | GUI 名称 |
| ------ | -------- | ------ | ------ |
| `input` | 必填 | 输入音频文件（支持 mp3、wav、flac、ogg、m4a、aac 等） | 拖入或点击打开 |
| `-o`, `--output` | `{原路径}/<输入名>.mid` | 输出 MIDI 文件路径 | 点击输出路径链接修改 |
| `--sr` | `22050` | 分析采样率（Hz） | 分析采样率(Hz) |
| `--n-fft` | `4096` | FFT 窗口大小，决定频率分辨率 | FFT窗口大小 |
| `--hop-length` | `256` | STFT 帧间跳跃采样数，决定时间分辨率 | STFT帧间跳采数 |
| `--threshold` | `20` | 峰值检测阈值（dB），低于帧最大值多少 dB 视为有效峰值 | 峰值阈值(dB) |
| `--max-notes` | `16` | 每帧最多同时检测的音符数 | 每帧最多音符数 |
| `--min-duration` | `30` | 最短音符时长（毫秒），更短的音符会被过滤 | 最短音符时长(ms) |
| `--dynamic-range` | `60` | 力度映射动态范围（dB） | 动态范围(dB) |
| `--no-piano-limit` | `false` | 禁用钢琴音域八度折叠，可能能提供更完整的听感，不过大多数预览器（包括本软件）都不太能显示出来，甚至因超音域无法播放 | 关闭钢琴音域折叠 |
| `--high-damp` | `0` | 高频力度衰减，实验性功能，不建议开启，可能反而导致转换效果变差 | 高频衰减(0~2) |
| `--mid-boost` | `0` | 中频力度增强，实验性功能，理论上可以增强人声听感，但是可能反而导致人声被模糊化 | 人声增强(0~2) |

理论上都是越大越好，当然越大生成速度越慢，而且也有可能适得其反，毕竟MIDI的音域远不如原始音频丰富。

---

### GUI 预览窗格

简单模拟许多 DAW 的钢琴卷帘（也可纵向），可以直接生成可视化的 MIDI 预览视频，除了软件直接导出的 MIDI，你也可以直接在主界面拖入，然后点击预览键，查看使用其余任意 DAW 导出的 MIDI 文件，默认都是使用的 0（钢琴）音色，点击播放选项可以调整设置：

不忽略钢琴音域外（88个Key）的音符，即可播放全音域文件，当然不保证所有的 SoundFont 都能播放全音域，预览也是看不到钢琴音域外的音符的。若需要播放 MIDI 文件中指定的音色，请勾选“使用对应音色匹配轨道”，而下方可以选择需要播放的轨道，未勾选的轨道不会播放也不会在预览窗格中显示，因为默认是仅钢琴音色，所以鼓组是未被勾选的，若勾选了“使用对应音色匹配轨道”，则完全可以把鼓组轨道也勾选上（除非你用的是正弦波），记得按确定保存设置，这个设置在导出的音视频中也能生效，此时就是原汁原味的 MIDI 播放器了。

若无 SoundFont（见上文）时预览与导出音视频只能是静音的，当 soundfonts 文件夹中有可用的 soundfont 则会自动选择，这时就能听到音色了。

导出音视频提供了丰富的编码与码率选项（见上文），功能依赖 FFmpeg 与 FluidSynth，请确保按照前文安装好依赖。

---

### 工作原理

1. **加载音频** — 通过 `soundfile/ffmpeg` 解码音频，并将输入音频转为单声道，统一采样率（默认 22.05 kHz）
2. **STFT 分析** — 使用 4096 点 FFT 进行短时傅里叶变换，获得约 5.4 Hz 的频率分辨率（`numpy/scipy`，`scipy` 在打包后包体默认不会被包含以减小体积）
3. **峰值检测** — 逐帧进行频谱峰值检测，自适应阈值 + 抛物线插值精确定位频率
4. **频率 → MIDI** — 将检测到的频率映射为 MIDI 音符号，超出钢琴音域（A0–C8）的频率按八度折叠（可开关）
5. **力度估计** — 根据频谱振幅映射为 MIDI 力度值（1–127）
6. **音符追踪** — 基于滞后的音符开/关检测，避免音符闪烁
7. **输出 MIDI** — 通过 `pretty_midi` 生成标准 `.mid` 文件

---

### 自行打包（仅支持 GUI）

项目使用 [Nuitka](https://nuitka.net/) 进行独立打包，可自行打包，Windows需要安装 Git Bash 运行打包以运行 `make` 等 GNU 工具，Linux务必安装好 `upx` 依赖：

```bash
# 安装nuitka
uv sync --extra dev
# 安装upx（Debian/Ubuntu）
sudo apt install upx
# 安装upx（Arch）
sudo pacman -S upx
```

```bash
# 打包
make package
```

Windows 打包时会自动下载 `upx` 用于给 `nuitka` 压缩，同时下载 `fluidsynth` 丢进二进制产物里面，且 `nuitka` 会下载 `zig` 作为编译工具，所以请自行保持良好的外网访问性。

打包后的可执行文件位于 `build/entry.dist/` 目录下。

---

## 许可证

MIT License

欢迎贡献提 issue 谢谢喵～

Star谢谢喵！！！
