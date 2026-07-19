# Agnes Video Creator

**自动生成短视频的 CLI 工具** — 基于 Agnes AI 三模型流水线：文本生成脚本 → 图像生成关键帧 → 视频生成片段 → ffmpeg 合成最终视频。

```bash
export AGNES_API_KEY="your_key"

# 一句话生成完整短视频
agnes-video create "一只猫探索未来城市" --style "cyberpunk" --duration 20
```

---

## 目录

- [概述](#概述)
- [安装](#安装)
- [快速开始](#快速开始)
- [命令参考](#命令参考)
- [工作流详解](#工作流详解)
- [三种视频生成模式](#三种视频生成模式)
- [完整示例](#完整示例)
- [分步工作流](#分步工作流)
- [配置](#配置)
- [依赖](#依赖)
- [项目结构](#项目结构)

---

## 概述

`agnes-video-creator` 将 Agnes AI 的三个模型编排为一个完整的视频生产流水线：

| 步骤 | 模型 | 用途 |
|------|------|------|
| **参考分析** | `agnes-2.0-flash` (vision) | 从参考视频中提取视觉风格（色彩、光照、运镜等） |
| **脚本** | `agnes-2.0-flash` | 根据主题或参考风格生成结构化分镜脚本 |
| **图像** | `agnes-image-2.1-flash` | 为每个场景生成关键帧图像 |
| **视频** | `agnes-video-v2.0` | 将文本/图像转换为视频片段（异步 + 轮询） |
| **合成** | ffmpeg | 拼接片段 + 转场 + 可选 TTS 旁白 |

**支持三种视频生成策略：**
- `text-to-video`：纯文本生成（最快）
- `image-to-video`：先图后视频（质量最佳，默认）
- `keyframes`：多帧动画过渡（叙事感最强）

**支持参考视频风格迁移：**
- `ref-create`：分析参考视频的视觉风格，用相同风格生成新内容

---

## 安装

### 前置条件

- **Python 3.10+**
- **ffmpeg** — 视频合成必需
  ```bash
  # macOS
  brew install ffmpeg

  # Ubuntu/Debian
  sudo apt install ffmpeg

  # Arch Linux
  sudo pacman -S ffmpeg
  ```

### 安装项目

```bash
git clone <repo-url> agnes-video-creator
cd agnes-video-creator

python3 -m venv .venv
source .venv/bin/activate

pip install -e .
```

验证安装：

```bash
agnes-video --help
```

---

## 快速开始

### 1. 设置 API Key

```bash
export AGNES_API_KEY="your_agnes_api_key"
```

API Key 可从 [apihub.agnes-ai.com](https://apihub.agnes-ai.com) 获取。也可通过 `--api-key` 参数传入。

### 2. 全流程生成

```bash
agnes-video create "a spaceship landing on an alien planet at dawn" \
  --style "cinematic sci-fi" \
  --duration 20 \
  --mode image-to-video
```

首次运行会依次：
1. 调用 `agnes-2.0-flash` 生成分镜脚本
2. 调用 `agnes-image-2.1-flash` 生成每个场景的关键帧图
3. 调用 `agnes-video-v2.0` 生成每个场景的视频片段（自动轮询等待完成）
4. ffmpeg 拼接片段 + 交叉淡化转场 + 可选 TTS 旁白

最终视频输出到 `agnes_video_output/<title>.mp4`。

### 3. 仅查看脚本

```bash
agnes-video init "深海生物发光奇观" --style "nature documentary" --duration 15
```

查看生成的 `agnes_video_output/script.json`，确认分镜满意后再继续。

---

## 命令参考

### `agnes-video init <topic>`

根据主题生成结构化分镜脚本。

```
选项：
  --style STYLE      视觉风格提示（如 "cyberpunk", "nature documentary"）
  --duration SECONDS 目标时长（秒），默认 15.0
```

输出：`agnes_video_output/script.json`

### `agnes-video scenes <script>`

为脚本中每个场景生成关键帧图像。

```
参数：
  script  脚本 JSON 文件路径
```

调用 `agnes-image-2.1-flash`，下载图像到 `output/images/`。

### `agnes-video render <script>`

为每个场景生成视频片段。

```
选项：
  --mode {text-to-video,image-to-video,keyframes}  生成模式（默认 image-to-video）
  --no-poll                                        不等待完成（仅创建任务）
```

调用 `agnes-video-v2.0`，自动轮询直到所有片段生成完毕。下载到 `output/videos/`。

### `agnes-video assemble <script>`

将所有视频片段合成为最终视频。

```
选项：
  --output, -o FILENAME  输出文件名
```

ffmpeg 拼接 + 转场 + TTS 旁白（如已安装 edge-tts）。

### `agnes-video create <topic>`

**全流程自动执行**：init → scenes → render → assemble。

```
选项：
  --style STYLE             视觉风格
  --duration SECONDS        目标时长
  --mode MODE               视频生成模式
  --output, -o FILENAME     输出文件名
  --no-poll                 不等待视频完成
  --skip-images             跳过图像生成
  --skip-video              跳过视频生成
  --skip-assembly           跳过合成
```

### `agnes-video ref-create <reference> <topic>`

**参考视频风格迁移** — 分析一个参考视频的视觉风格，用相同风格生成关于新主题的视频。

```
参数：
  reference             参考视频文件路径（MP4、MOV 等）
  topic                 新视频内容描述

选项：
  --ref-frames N        从参考视频中抽取的帧数（默认 3）
  --duration SECONDS    目标时长（秒），默认 15.0
  --style STYLE         附加风格提示（与参考风格融合）
  --mode MODE           视频生成模式
  --output, -o FILENAME 输出文件名
  --no-poll             不等待视频完成
  --skip-images         跳过图像生成
  --skip-video          跳过视频生成
  --skip-assembly       跳过合成
```

工作流：
1. ffmpeg 从参考视频中抽取 N 帧关键画面
2. `agnes-2.0-flash` 视觉分析帧画面，提取详细风格画像（色彩、光照、运镜、构图、情绪、运动特征）
3. 基于风格画像 + 用户主题，生成风格一致的分镜脚本
4. 后续同 `create`：scenes → render → assemble

### `agnes-video status <script>`

查看脚本各场景的完成状态（哪些已有图像/视频）。

---

## 工作流详解

### 脚本生成（init）

调用 `agnes-2.0-flash`，使用精心设计的 system prompt，输出结构化的 JSON 分镜：

```json
{
  "title": "Alien Planet Landing",
  "description": "A spaceship descends onto an alien world at dawn",
  "total_duration": 20.0,
  "scenes": [
    {
      "id": 1,
      "narration": "As the first light of dawn breaks over the horizon...",
      "visual_prompt": "A vast alien landscape with purple crystal formations...",
      "duration_seconds": 6.0,
      "camera": "slow establishing pan",
      "style": "cinematic sci-fi"
    }
  ]
}
```

每条 `visual_prompt` 都包含：主体、动作、环境、光照、镜头运动、风格和质量要求 — 可直接用于图像/视频生成。

### 图像生成（scenes）

使用 `agnes-image-2.1-flash`，设置：
- `size: "2K"`, `ratio: "16:9"` — 高质量宽屏输出
- 自动翻译非英文 prompt
- 下载图像到本地供后续图生视频使用

### 视频生成（render）

使用 `agnes-video-v2.0`，异步任务模式：
1. `POST /v1/videos` 创建任务
2. 每 10 秒轮询 `GET /v1/videos/{task_id}`
3. 完成后下载 MP4 到本地

视频参数：`1152x768`, `121 帧`, `24fps`（约 5 秒/片段）。

### 视频合成（assemble）

ffmpeg 执行：
1. 归一化所有片段到统一编码（h264 yuv420p）
2. 用 `xfade` 滤镜实现交叉淡化转场
3. 可选：用 `edge-tts` 生成旁白并混音

---

## 三种视频生成模式

### 1. text-to-video（纯文本）

```
agnesis-video create "主题" --mode text-to-video
```

直接从文本 prompt 生成视频。最快但一致性最低。适合概念预览。

### 2. image-to-video（图生视频，默认）

```
agnesis-video create "主题" --mode image-to-video
```

先生成关键帧图像，再根据图像 + 文本生成视频。主体和场景一致性最佳。**推荐用于大多数场景。**

### 3. keyframes（关键帧动画）

```
agnesis-video create "主题" --mode keyframes
```

生成多张连续关键帧图像，模型在帧之间生成平滑过渡动画。最适合有明确叙事结构的视频（开场→发展→高潮→结尾）。

---

## 完整示例

### 示例 1：中文主题，自然纪录片风格

```bash
export AGNES_API_KEY="your_key"

agnes-video create "一只雪豹在青藏高原上捕猎" \
  --style "BBC wildlife documentary" \
  --duration 25 \
  --mode image-to-video \
  --output snow_leopard.mp4
```

自动将中文 prompt 翻译为英文后传给图像/视频 API。最终视频输出为 `snow_leopard.mp4`。

### 示例 2：分步控制

```bash
# 1. 生成脚本
agnes-video init "a cyberpunk city street at night, neon lights reflecting on wet pavement" \
  --style "blade runner aesthetic" \
  --duration 30

# 2. 查看脚本内容
cat agnes_video_output/script.json

# 3. 生成场景图像
agnes-video scenes agnes_video_output/script.json

# 4. 用关键帧模式生成视频（需要至少 2 个场景）
agnes-video render agnes_video_output/script.json --mode keyframes

# 5. 合成最终视频
agnes-video assemble agnes_video_output/script.json --output cyberpunk_city.mp4

# 6. 查看完成状态
agnes-video status agnes_video_output/script.json
```

### 示例 3：参考视频风格迁移

```bash
# 用一段电影级航拍视频的风格，生成"沙漠日出"主题的新视频
agnes-video ref-create reference_aerial.mp4 "金色沙漠中的骆驼商队在日出时前行" \
  --duration 20 \
  --ref-frames 4 \
  --mode image-to-video \
  --output desert_caravan.mp4
```

流程：
1. 从 `reference_aerial.mp4` 抽取 4 帧关键画面
2. 视觉模型分析其色彩（暖金色）、光照（黄金时刻）、运镜（缓慢航拍推近）、构图（三分法+引导线）、情绪（壮阔宁静）
3. 在相同风格下生成关于"沙漠商队"的分镜脚本
4. 完成后续图像→视频→合成

### 示例 4：批量创建但不等待

```bash
# 只创建视频任务，不轮询—适合后台批量提交
agnes-video render script.json --no-poll
# 稍后查看状态
agnes-video status script.json
```

---

## 配置

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AGNES_API_KEY` | API Key（必填） | — |
| `AGNES_API_TOKEN` | 同上（备选） | — |
| `APIHUB_AGNES_API_KEY` | 同上（备选） | — |
| `AGNES_IMAGE_SIZE` | 图像尺寸档位 | `2K` |
| `AGNES_IMAGE_RATIO` | 图像宽高比 | `16:9` |
| `AGNES_VIDEO_WIDTH` | 视频宽度 | `1152` |
| `AGNES_VIDEO_HEIGHT` | 视频高度 | `768` |
| `AGNES_OUTPUT_DIR` | 输出目录 | `agnes_video_output` |
| `AGNES_TRANSLATE` | 是否自动翻译非英文 prompt | `1` |
| `AGNES_AUDIO` | 是否添加 TTS 旁白 | `1` |

---

## 依赖

### 运行时

| 依赖 | 用途 | 安装方式 |
|------|------|----------|
| Python 3.10+ | 运行环境 | — |
| ffmpeg | 视频合成（必需） | 系统包管理器 |
| edge-tts | TTS 旁白（可选） | `pip install edge-tts` |

### Agnes API 要求

- 有效的 Agnes AI API Key（当前免费）
- 网络可访问 `https://apihub.agnes-ai.com`

---

## 项目结构

```
agnes-video-creator/
├── pyproject.toml                      # 项目配置 + 入口点
├── README.md                           # 本文档
└── src/agnes_video_creator/
    ├── __init__.py                     # 包标记
    ├── models.py                       # Script / Scene 数据模型
    ├── config.py                       # API Key、默认参数、环境变量
    ├── utils.py                        # HTTP 请求、轮询、翻译、文件工具
    ├── reference.py                    # 参考视频帧提取 + 视觉风格分析 + 风格迁移脚本
    ├── script_generator.py             # Agnes 2.0 Flash → 分镜脚本
    ├── image_generator.py              # Agnes Image 2.1 Flash → 关键帧图像
    ├── video_generator.py              # Agnes Video V2.0 → 视频片段
    ├── assembler.py                    # ffmpeg 合成 + 转场 + TTS
    └── cli.py                          # CLI 入口（7 个命令）
```

---

## License

MIT
