# Agnes Video Creator

**基于小说自动生成短视频短剧的全流程工具** — 从 TXT 小说到多集短视频短剧，支持分镜 → 图像 → 视频 → 合成全自动化流水线。

```
输入：小说文本 → 输出：多集短视频短剧（含角色声音、字幕、转场、背景音乐）
```

基于 Agnes AI 三模型编排 + ffmpeg 合成。

```bash
export AGNES_API_KEY="your_key"

# 小说 → 多集短剧（全自动）
agnes-video project init my-drama --novel novel.txt
agnes-video project analyze my-drama
agnes-video project render my-drama
agnes-video project assemble my-drama

# Web 图形界面
agnes-video ui

# 一句话生成完整短视频
agnes-video create "一只猫探索未来城市" --style "cyberpunk" --duration 20
```

---

## 目录

- [概述](#概述)
- [安装](#安装)
- [快速开始：小说转短剧](#快速开始小说转短剧)
- [命令参考](#命令参考)
- [Web UI](#web-ui)
- [批处理队列](#批处理队列)
- [角色表](#角色表)
- [场景裁剪](#场景裁剪)
- [导出预设](#导出预设)
- [成本估算](#成本估算)
- [一致性检查](#一致性检查)
- [面部特征分析与面部锁定](#面部特征分析与面部锁定)
- [跨集连续性](#跨集连续性)
- [分镜预览](#分镜预览)
- [配置](#配置)
- [开发](#开发)
- [依赖](#依赖)
- [项目结构](#项目结构)

---

## 概述

完整流水线：

| 步骤 | 模型/工具 | 用途 |
|------|-----------|------|
| **小说分析** | `agnes-2.0-flash` | 将 TXT 小说拆分为多集分镜脚本 |
| **剧本生成** | `agnes-2.0-flash` | 每集生成结构化分镜（含角色、对白、镜头） |
| **角色塑形** | `agnes-image-2.1-flash` | 为每个角色生成参考肖像 + 面部特征 |
| **图像生成** | `agnes-image-2.1-flash` | 每个场景生成关键帧图像 |
| **视频生成** | `agnes-video-v2.0` | 文本/图像 → 视频片段（异步 + 轮询） |
| **合成** | ffmpeg | 拼接片段 + 转场 + TTS 旁白 + 字幕 + BGM |
| **导出** | ffmpeg | 裁剪为 9:16 竖版 / 1:1 方版等分发格式 |

**视频生成模式：**
- `text-to-video`：纯文本（最快）
- `image-to-video`：先图后视频（质量最佳，**默认**）
- `keyframes`：多帧过渡（叙事感最强）

**项目化管理：**
- 多集项目结构（每集独立脚本 + 图像 + 视频）
- `--resume` 断点续传（从上次中断位置恢复）
- `--scene` 单场景重生成
- 并行渲染（多集同时生成，提升效率）
- 批处理队列（SQLite 持久化，后台自动执行）
- **Web 图形界面**（项目看板 + 编辑 + 监控 + 批处理管理）

---

## 安装

### 前置条件

- **Python 3.10+**
- **ffmpeg** — 必需
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

## 快速开始：小说转短剧

### 1. 设置 API Key

```bash
export AGNES_API_KEY="your_agnes_api_key"
```

可从 [apihub.agnes-ai.com](https://apihub.agnes-ai.com) 获取。

### 2. 初始化项目

```bash
# 创建一个项目，关联小说文件
agnes-video project init my-drama --novel story.txt --style "古风武侠"
```

输出：`projects/my-drama/project.json`，小说被复制到项目目录。

可选参数：
- `--style`：视觉风格描述（如 "cyberpunk", "nature documentary"）
- `--mood`：情绪基调描述
- `--target`：目标观众描述
- `--episodes`：目标集数（默认自动）

### 3. 分析与拆解

```bash
# 分析小说并生成多集脚本
agnes-video project analyze my-drama
```

此步骤会：
1. 调用 LLM 分析小说结构（章节、角色、情节线）
2. 拆解为多集（每集约 10-15 场景）
3. 为每个角色生成外貌描述和参考肖像
4. 输出每集的 `script.json`

### 4. 渲染视频

```bash
# 为所有集生成图像 + 视频（支持并行）
agnes-video project render my-drama
```

可单独渲染某集：

```bash
agnes-video project render my-drama --episode 1
```

### 5. 合成最终视频

```bash
# 合成所有集
agnes-video project assemble my-drama
```

### 6. Web 图形界面管理

```bash
# 启动 Web UI（更直观的项目管理）
agnes-video ui
```

然后在浏览器中打开 `http://localhost:8700` 查看项目看板。

---

## 命令参考

### `agnes-video init <topic>`

根据主题生成单视频的分镜脚本。

```
选项：
  --style STYLE        视觉风格提示
  --duration SECONDS   目标时长（秒），默认 15.0
```

输出：`agnes_video_output/script.json`

### `agnes-video scenes <script>`

为脚本中每个场景生成关键帧图像。

```
参数：
  script  脚本 JSON 文件路径
```

### `agnes-video render <script>`

为每个场景生成视频片段。

```
选项：
  --mode MODE    生成模式（text-to-video / image-to-video / keyframes）
  --no-poll      不等待完成（仅创建任务）
```

### `agnes-video assemble <script>`

将所有视频片段合成为最终视频。

```
选项：
  --output, -o FILENAME  输出文件名
```

### `agnes-video create <topic>`

**全流程自动执行**：init → scenes → render → assemble。

```
额外选项：
  --mode MODE        视频生成模式
  --output, -o FILE  输出文件名
  --no-poll          不等待视频完成
  --skip-images      跳过图像生成
  --skip-video       跳过视频生成
  --skip-assembly    跳过合成
  --resume           从上次中断处恢复
  --scene N          仅重新生成指定场景
  --voice-map KEY=VAL 为角色指定 TTS 声音（如 "林黛玉=zh-CN-XiaoxiaoNeural"）
```

### `agnes-video ref-create <reference> <topic>`

**参考视频风格迁移** — 分析参考视频的视觉风格，用相同风格生成新视频。

```
选项：
  --ref-frames N   从参考视频中抽取的帧数（默认 3）
  --duration SEC   目标时长（秒），默认 15.0
  --mode MODE      视频生成模式
  --output, -o     输出文件名
```

工作流：抽取帧 → 视觉风格分析 → 脚本生成 → 图像 → 视频 → 合成。

### `agnes-video project`

**项目管理命令组** — 多集短剧全流程管理。

```
子命令：
  init <name>         创建新项目（可选：--novel, --style, --mood, --target, --episodes）
  status <name>       查看项目各集状态
  analyze <name>      分析小说并生成所有集脚本（可选：--episode N 单集）
  novel <name>        仅导入/更新小说（不触发生成）
  render <name>       为所有集生成图像 + 视频（可选：--episode N, --parallel）
  assemble <name>     合成所有集视频
  check <name>        检查所有集的情节一致性

示例：
  agnes-video project init wuxia --novel novel.txt --style "古风武侠"
  agnes-video project analyze wuxia
  agnes-video project render wuxia --parallel
  agnes-video project assemble wuxia
  agnes-video project status wuxia
  agnes-video project check wuxia
```

### `agnes-video check <script> [script...]`

**情节一致性检查** — 使用 LLM 分析剧本中的角色、时间线、剧情连续性问题。

```
参数：
  script  一个或多个脚本 JSON 文件路径

选项：
  --project  检查当前项目中的所有集

示例：
  agnes-video check output/script.json
  agnes-video check --project
```

输出问题列表：
- `critical` — 严重的连续性错误（角色名字不同、时间线矛盾）
- `warning` — 潜在的一致性问题（风格变化、视觉矛盾）
- `info` — 建议性提示

### `agnes-video batch`

**批处理队列命令组** — 持久化 SQLite 作业队列，后台自动执行。

```
子命令：
  submit <job_type> [project] [--episode N]  提交作业
    job_type: analyze / render_all / render_episode / check
  list [--project NAME] [--limit N]           列出作业
  status <job_id>                             查看单个作业详情
  cancel <job_id>                             取消待处理或运行中的作业
  
示例：
  agnes-video batch submit analyze my-drama
  agnes-video batch submit render_all my-drama
  agnes-video batch list
  agnes-video batch cancel abc123
```

队列使用 `~/.agnes-video/batch.db` 持久化，即使在 CLI 重启后仍然存在。

### `agnes-video status <script>`

查看脚本各场景的完成状态（哪些已有图像/视频）。

### `agnes-video ui`

**启动 Web 图形界面**。

```
选项：
  --port PORT   端口号（默认 8700）
  --host HOST   监听地址（默认 127.0.0.1）
```

Web UI 提供：
- 项目看板（创建、查看、管理项目）
- 每集详细视图（场景卡片、视频预览、字幕编辑）
- 角色表编辑器（名称、年龄、性格、外貌、对白样本、TTS 声音）
- 场景裁剪（设置每个视频片段的入/出点）
- 导出预设（一键裁剪为 9:16 竖版、1:1 方版等）
- 成本估算（显示预计费用和时间）
- 情节一致性检查
- 批处理队列管理（提交、监控、取消作业）
- 内联场景编辑（旁白、视觉提示、时长）
- 实时流水线日志（SSE 流）

---

## Web UI

**`agnes-video ui`** 启动一个完整的 Web 管理界面（FastAPI + SPA）。

### 功能概述

| 页面 | 功能 |
|------|------|
| **项目看板** | 列出所有项目、创建新项目、查看各集状态摘要 |
| **项目详情** | 各集表格、故事版链接、角色表编辑器、控制面板、批处理提交 |
| **集详情** | 场景网格（图像/视频预览）、内联编辑、裁剪、导出、成本估算 |
| **批处理队列** | 实时更新的作业列表、进度状态、取消作业 |

### 角色表编辑器

在项目详情页的侧边栏中，每个角色展示为可展开的卡片，支持编辑：

- **角色名称、身份**（主角/反派/配角）
- **年龄**（如 "mid-20s"、"elderly"）
- **TTS 声音**（如 `zh-CN-XiaoxiaoNeural`）
- **性格**（用于对白生成风格一致）
- **外貌描述**（注入到图像生成 prompt）
- **对白样本**（示例台词）
- **背景故事**（便于情节连续性检查）

点击 **Save All** 保存所有更改。

### 场景裁剪

在集详情页，每个场景卡片底部有 **Trim** 控件。输入要从视频片段 **开头** 和 **结尾** 裁剪的秒数，点击 **Trim**。裁剪是**非破坏性**的——下次合成时会自动应用。

### 导出预设

在集详情页的控制面板中，点击 **9:16** / **1:1** / **4:3** 按钮以对应比例裁剪视频。裁剪使用居中裁剪算法，保持视频高度不变。导出文件保存为 `{原文件名}_{比例}.mp4`。

支持的比例：
- `16:9` — 宽屏（原始）
- `9:16` — 竖版（TikTok / Reels）
- `1:1` — 方版（Instagram）
- `4:3` — 经典
- `21:9` — 超宽影院

### 成本估算

在集详情页的侧边栏中，**Cost Estimate** 卡片显示基于场景数量和流水线阶段的预计费用和时间。估算是近似的，基于当前的 Agnes AI 定价配置（可在 `cost_estimator.py` 中调整）。

---

## 批处理队列

批处理队列使用 SQLite 持久化存储（`~/.agnes-video/batch.db`），支持后台自动执行。

### 工作方式

1. 提交作业到队列（CLI 或 Web UI）
2. 工作线程自动取出并执行
3. 完成后更新状态
4. 可通过 CLI 或 Web UI 查看状态

### 作业状态

| 状态 | 含义 |
|------|------|
| `pending` | 等待执行 |
| `running` | 正在执行 |
| `completed` | 已完成 |
| `failed` | 失败（含错误信息） |
| `cancelled` | 已取消 |

### 示例

```bash
# CLI 提交
agnes-video batch submit analyze my-drama
agnes-video batch submit check my-drama
agnes-video batch list

# Web UI 提交：在项目详情页的 Batch Queue 区选择作业类型
```

---

## 角色表

角色表存储每个角色的详细描述，用于：
- 使 LLM 生成的对白风格一致
- 在图像生成中保持角色外貌一致
- 在连续性检查中验证角色关系

### 字段说明

| 字段 | 用途 |
|------|------|
| `name` | 角色名字（唯一标识） |
| `role` | 身份（主角/反派/配角） |
| `age` | 年龄描述 |
| `voice` | TTS 声音名称 |
| `personality` | 性格特征 |
| `appearance` | 外貌描述（注入到图像 prompt） |
| `sample_dialogue` | 示例台词 |
| `backstory` | 背景故事 |
| `portrait_url` | 参考肖像 URL |

### API

- `GET /api/projects/{name}/characters` — 获取所有角色
- `PUT /api/projects/{name}/characters` — 批量更新角色

---

## 场景裁剪

### 命令行

裁剪是合成流程的一部分——在场景视频被送入合成流水线前自动应用 `trim_in` / `trim_out`。

通过 API 设置裁剪参数：

```bash
# 通过 API 设置裁剪
curl -X PUT http://localhost:8700/api/projects/my-drama/episodes/1/scene/3/trim \
  -H "Content-Type: application/json" \
  -d '{"trim_in": 0.5, "trim_out": 1.0}'
```

### 工作原理

1. `trim_in` = 从视频开头裁剪的秒数
2. `trim_out` = 从视频结尾裁剪的秒数
3. 裁剪使用 `ffmpeg -ss -t -c copy`（快速，不重新编码）
4. 如果裁剪后片段长度为 0，则忽略裁剪（保持原始）

---

## 导出预设

### 命令行

```bash
# 通过 API 导出
curl -X POST http://localhost:8700/api/projects/my-drama/episodes/1/export \
  -H "Content-Type: application/json" \
  -d '{"aspect": "9:16"}'
```

### 编程使用

```python
from agnes_video_creator.assembler import export_crop
from pathlib import Path

export_crop(
    src=Path("output/episode.mp4"),
    dst=Path("output/episode_9x16.mp4"),
    aspect="9:16",    # 支持 16:9, 9:16, 1:1, 4:3, 21:9
)
```

### 裁剪算法

- 保持视频高度不变
- 居中裁剪宽度以匹配目标比例
- 使用 `ffmpeg` 的 `crop` 滤镜
- 输出使用 H.264 + AAC，与原始视频格式一致

---

## 成本估算

### 命令行

```bash
# 通过 API 获取单集估算
curl http://localhost:8700/api/projects/my-drama/estimate/1

# 获取项目整体估算
curl http://localhost:8700/api/projects/my-drama/estimate
```

### 定价配置

在 `cost_estimator.py` 中可调整：

```python
PRICE_PER_IMAGE = 0.04         # agnes-image-2.1-flash
PRICE_PER_VIDEO_CLIP = 0.10    # agnes-video-v2.0
PRICE_PER_TEXT_CALL = 0.002    # agnes-2.0-flash
```

### 返回格式

```json
{
  "images": 10,
  "video_clips": 10,
  "text_calls": 1,
  "cost_images": 0.40,
  "cost_videos": 1.00,
  "cost_text": 0.00,
  "total_cost": 1.40,
  "total_time_seconds": 1170.0
}
```

---

## 一致性检查

使用 LLM 分析剧本中的一致性问题：

### 检查内容

- **角色一致性**：名字拼写、性格、关系、声音是否一致
- **时间线**：时间顺序是否合理
- **情节漏洞**：是否有未解释的事件或矛盾
- **视觉连续性**：环境描述是否前后矛盾
- **对白一致性**：角色用词风格是否一致

### 输出等级

| 等级 | 图标 | 含义 |
|------|------|------|
| `critical` | ✗ | 需要修复的严重问题 |
| `warning` | ⚠ | 潜在一致性问题 |
| `info` | ℹ | 建议性提示 |

---

## 面部特征分析与面部锁定

使用 `agnes-2.0-flash` 视觉模型对角色肖像进行**法医级面部特征分析**，提取结构化描述并自动注入到每个场景的图像生成 prompt 中，确保角色面部在所有场景中保持一致。

### 工作流程

```
角色肖像 → 视觉模型分析 → FaceFeatures 结构化数据 → 注入场景 prompt → 一致的面部输出
```

1. 在 `project analyze` 阶段为每个角色生成参考肖像
2. `face_analyzer.analyze_face()` 调用视觉模型分析肖像
3. 提取 14 个维度的面部特征（脸型、眼型、肤色、发型等）
4. 特征以自然语言描述形式注入到每个场景的 `visual_prompt`
5. 图像/视频模型在生成时参考这些特征，实现**面部锁定**

### 提取的面部维度

| 维度 | 字段 | 示例值 |
|------|------|--------|
| 脸型 | `face_shape` | oval / round / square / heart |
| 眼型 | `eye_shape` | almond / hooded / monolid |
| 瞳色 | `eye_color` | dark_brown / blue / green |
| 眉形 | `eyebrow` | straight / arched / thick |
| 鼻型 | `nose` | straight / aquiline / button |
| 唇型 | `mouth_lips` | full / thin / wide |
| 下颌 | `jaw_chin` | strong_jaw / pointed_chin |
| 肤色 | `skin_tone` | fair / light / tan / dark |
| 肤质 | `skin_texture` | smooth / freckled / weathered |
| 发型 | `hair_style` | short / long / curly / wavy |
| 发色 | `hair_color` | black / brown / blonde |
| 年龄段 | `age_range` | young_adult / middle_aged / elderly |
| 性别表现 | `gender_presentation` | masculine / feminine |
| 特征标记 | `distinctive_features` | ["scar_on_left_cheek", "glasses"] |

### 编程使用

```python
from agnes_video_creator.face_analyzer import analyze_face
from agnes_video_creator.config import AgnesConfig

cfg = AgnesConfig(api_key="your_key")
features = analyze_face("https://example.com/portrait.jpg", cfg)

# 转换为 prompt 片段
prompt_snippet = features.to_prompt_snippet()
# 输出: "脸型: 鹅卵形, 眼型: 杏仁眼, 瞳色: 深棕, ..."

# 快速验证肖像是否包含人脸
from agnes_video_creator.face_analyzer import validate_portrait_face
has_face = validate_portrait_face("https://example.com/portrait.jpg", cfg)
```

### `FaceFeatures` 数据模型

```python
@dataclass
class FaceFeatures:
    face_shape: str = ""
    eye_shape: str = ""
    eye_color: str = ""
    eyebrow: str = ""
    nose: str = ""
    mouth_lips: str = ""
    jaw_chin: str = ""
    skin_tone: str = ""
    skin_texture: str = ""
    hair_style: str = ""
    hair_color: str = ""
    age_range: str = ""
    gender_presentation: str = ""
    distinctive_features: list[str] = field(default_factory=list)

    def to_prompt_snippet(self) -> str:
        """格式化为自然语言描述，用于注入图像生成 prompt"""

    def is_populated(self) -> bool:
        """检查是否提取到了足够的特征"""
```

---

## 跨集连续性

多集短剧流水线中，`continuity.py` 负责在集与集之间传递**角色状态**和**视觉注册表**，使每一集的情节和视觉风格建立在上一集的基础上，而不是从头开始。

### 核心数据结构

**角色连续性** — 每个角色在每集结束时的状态：

| 字段 | 含义 |
|------|------|
| `outfit` | 当前服装描述（如 "蓝色道袍"） |
| `location` | 当前位置（如 "华山之巅"） |
| `emotional_state` | 情绪状态（如 "愤怒"、"悲伤"） |
| `notes` | 自由格式笔记（如 "右臂受伤"） |

**视觉注册表** — 跨集复用的视觉描述：

| 类型 | 用途 |
|------|------|
| `environments` | 场景场所描述（如 "青云门大殿"） |
| `props` | 道具物品描述（如 "轩辕剑"） |
| `outfits` | 角色服装描述（如 "夜行衣"） |

**剧情线索** — 当前活跃的剧情线列表，确保子情节不会丢失。

### 工作流程

```
Episode 1 ──→ ContinuityState ──→ Episode 2 prompt ──→ Episode 2
                                                  ↓
                                        ContinuityState updated
                                                  ↓
                                        Episode 3 prompt ...
```

1. 在 `project analyze` 开始时创建空的 `ContinuityState`
2. 为 Episode 1 生成脚本后，LLM 返回 `visual_updates`（服装/地点/情绪变更）
3. `apply_visual_updates()` 更新连续性状态
4. Episode 2 的生成 prompt 中注入前情提要和视觉参考
5. 重复此过程直到所有集完成

### 生成的 Prompt 示例

```
前情提要:
主角在青云门大殿击败了黑衣人，救出了师妹。

当前活跃剧情线索:
  - 黑衣人幕后主使的身份
  - 师妹体内封印的魔气

已知视觉参考 (新场景中请复用这些描述):
  场景场所:
    - 青云门大殿: 青石铺地，高悬牌匾"青云"
  道具物品:
    - 轩辕剑: 三尺青锋，剑身刻有龙纹
  角色服装:
    - 主角: 蓝色道袍，左袖破损

角色当前状态:
  主角:
    当前服装: 蓝色道袍 (左袖破损)
    当前位置: 青云门大殿
    情绪状态: 愤怒
```

---

## 分镜预览

在图像生成完成后、视频生成之前，`storyboard.py` 生成一个**独立的 HTML 故事板**，让用户预览所有场景的图像、旁白和对话，及早发现问题，避免在问题场景上浪费 API 配额。

### 工作方式

```bash
# 故事板预览嵌入在渲染流程中：
# 生成图像 → 生成 HTML 故事板 → 用户确认 → 生成视频
```

HTML 故事板包含：
- 所有场景的缩略图（16:9 网格布局）
- 场景编号、时长、摄像机运动标签
- 旁白文字
- 对话列表（角色名 + 台词）
- 出镜角色标签
- 角色列表

### 用户交互

当故事板被生成后，CLI 会提示：

```
📋 Storyboard: /path/to/storyboard.html
检查分镜中角色的面部一致性。
继续生成视频? [Y/n]:
```

- 输入 `Y` 或回车 → 继续视频生成
- 输入 `n` → 暂停。用户可以编辑场景图像，然后用 `--skip-images` 跳过图像步骤继续

### 编程使用

```python
from agnes_video_creator.storyboard import generate_storyboard_html
from agnes_video_creator.models import Script

script = Script.load("projects/my-drama/episodes/1/script.json")
html_path = generate_storyboard_html(script, "storyboard.html")
# → 打开 storyboard.html 查看
```

### 设计目的

- **节省 API 费用**：在投入视频生成之前发现角色面部不一致、构图问题
- **加速迭代**：图像生成（秒级）远比视频生成（分钟级）便宜，先审图像再生成视频
- **团队协作**：HTML 文件可分享给非技术成员评审

---

## 开发

### 安装开发依赖

```bash
pip install -e ".[dev]"
```

这安装：
- `ruff` — 代码检查与格式化
- `pytest` — 测试框架

### 运行测试

```bash
# 运行全部测试
pytest

# 运行单个测试模块
pytest tests/test_models.py

# 带覆盖率报告
pytest --cov=agnes_video_creator --cov-report=term-missing
```

### 代码风格

使用 [Ruff](https://docs.astral.sh/ruff/) 进行 lint 和格式化，配置在 `pyproject.toml` 中：

```bash
# 检查代码问题
ruff check src/

# 自动修复
ruff check --fix src/

# 格式化代码
ruff format src/
```

规则集：`E`、`F`、`W`（核心）、`I`（导入排序）、`N`（命名）、`UP`（pyupgrade）、`B`（bugbear）、`SIM`（简化）。

行长度：**100 字符**。

### 项目结构

参见下方的[项目结构](#项目结构)章节。

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
| `AGNES_SUBTITLES` | 是否生成字幕 | `1` |
| `AGNES_AUDIO_LANG` | 音频语言 | `zh` |
| `AGNES_TTS_VOICE` | 默认 TTS 声音 | `zh-CN-XiaoxiaoNeural` |
| `AGNES_SUBTITLE_FONT` | 字幕字体路径 | 自动检测 |
| `AGNES_SUBTITLE_SIZE` | 字幕字号 | `28` |
| `AGNES_SUBTITLE_COLOR` | 字幕颜色 | `white` |
| `AGNES_SUBTITLE_POSITION` | 字幕位置 | `bottom` |
| `AGNES_BGM_PATH` | BGM 文件路径 | 空（无 BGM） |
| `AGNES_BGM_VOLUME` | BGM 音量（相对旁白） | `0.08` |
| `AGNES_BGM_FADE_IN` | BGM 淡入秒数 | `2.0` |
| `AGNES_BGM_FADE_OUT` | BGM 淡出秒数 | `3.0` |
| `AGNES_BGM_DUCKING` | 旁白时自动压低 BGM | `1` |
| `AGNES_BGM_DUCK_THRESHOLD` | 侧链压缩阈值（dB） | `-25.0` |
| `AGNES_METADATA` | 嵌入生成元数据 | `1` |
| `AGNES_CHAPTERS` | 添加 YouTube 章节标记 | `1` |
| `AGNES_THUMBNAIL` | 提取缩略图 | `1` |
| `AGNES_SFX_DIR` | 场景音效目录 | 空（无音效） |
| `AGNES_CACHE` | 启用生成缓存 | `1` |
| `AGNES_CACHE_DIR` | 缓存目录覆盖 | `~/.agnes-video/cache` |
| `AGNES_WATERMARK_PATH` | 水印图片路径 | 空（无水印） |
| `AGNES_WATERMARK_POS` | 水印位置（top-left/right/bottom-left/right） | `bottom-right` |
| `AGNES_WATERMARK_OPACITY` | 水印不透明度（0-1） | `0.7` |
| `AGNES_WATERMARK_SCALE` | 水印缩放（相对视频宽度） | `0.1` |

### 命令行选项

全局选项（适用于所有子命令）：

| 选项 | 说明 |
|------|------|
| `--api-key KEY` | 指定 API Key（覆盖环境变量） |
| `--output-dir DIR` | 输出目录 |
| `--quiet` | 静默模式，不输出进度信息 |

---

## 依赖

### 系统依赖

| 依赖 | 用途 | 安装方式 |
|------|------|----------|
| Python 3.10+ | 运行环境 | — |
| ffmpeg | 视频合成与裁剪（必需） | 系统包管理器 |

### Python 包

#### 核心（自动安装）

| 包 | 用途 |
|----|------|
| httpx | HTTP 请求（调用 Agnes API） |
| aiofiles | 异步文件操作 |

#### 可选依赖组

```bash
# Web UI
pip install -e ".[web]"

# TTS 配音
pip install -e ".[tts]"

# 开发
pip install -e ".[dev]"

# 全部
pip install -e ".[web,tts,dev]"
```

| 组 | 包 | 用途 |
|----|----|------|
| `[web]` | fastapi + uvicorn + pydantic | Web UI 后端 |
| `[tts]` | edge-tts | TTS 配音 |
| `[dev]` | ruff + pytest | 代码检查与测试 |

---

## 项目结构

```
agnes-video-creator/
├── pyproject.toml                     # 项目配置 + 入口点
├── README.md                          # 本文档
├── tests/                             # 测试套件（340+ 测试）
│   ├── test_models.py
│   ├── test_utils.py
│   ├── test_assembler.py
│   ├── test_novel.py
│   ├── test_portraits.py
│   ├── test_storyboard.py
│   ├── test_consistency.py
│   ├── test_project.py
│   └── conftest.py                    # 共享测试夹具
└── src/agnes_video_creator/
    ├── __init__.py
    ├── models.py                      # Script / Scene / Character 数据模型
    ├── config.py                      # API Key、默认参数、环境变量
    ├── utils.py                       # HTTP 请求、轮询、翻译、文件工具
    ├── reference.py                   # 参考视频帧提取 + 视觉风格分析
    ├── script_generator.py            # Agnes 2.0 Flash → 分镜脚本
    ├── image_generator.py             # Agnes Image 2.1 Flash → 关键帧图像
    ├── video_generator.py             # Agnes Video V2.0 → 视频片段
    ├── assembler.py                   # ffmpeg 合成 + 转场 + 字幕 + 裁剪导出
    ├── cli.py                         # CLI 入口（所有命令）
    ├── project.py                     # 项目管理（多集组织 + 并行调度）
    ├── novel.py                       # 小说分析与多集拆解
    ├── pipeline_state.py              # 流水线状态持久化（断点续传）
    ├── consistency.py                 # 情节一致性检查
    ├── continuity.py                  # 跨集连续性状态
    ├── portraits.py                   # 角色参考肖像生成
    ├── face_analyzer.py               # 面部特征提取
    ├── storyboard.py                  # 故事版预览生成
    ├── batch.py                       # 批处理队列（SQLite 持久化）
    ├── cost_estimator.py              # 成本估算
    ├── web_ui.py                      # FastAPI Web 后端
    └── web_app/
        └── index.html                 # SPA 前端（单页应用）
```

---

## License

MIT
