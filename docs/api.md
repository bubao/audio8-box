# API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 健康检查，加载已安装模型，返回后端/模型/采样率 |
| GET | `/v1/models` | 模型列表（OpenAI 风格） |
| POST | `/v1/audio/speech` | JSON 合成，字段见下方 |
| POST | `/v1/audio/speech/clone` | multipart 上传参考音频合成 |
| GET | `/api/voices` | 音色库列表（名称 / 参考文本 / 音频文件状态） |
| POST | `/api/voices` | 保存音色（multipart：`name` / `ref_text` / `file`） |
| DELETE | `/api/voices/{voice_id}` | 删除音色（含参考音频文件） |
| GET | `/api/models` | 模型安装状态（含每个源，Web 用） |
| POST | `/api/models/install` | 启动后台下载，返回 `task_id` |
| GET | `/api/models/tasks/{task_id}` | 下载进度 |
| DELETE | `/api/models/{name}?source=hf&yes=true` | 删除模型缓存（`source` 可选，默认取模型默认源） |
| POST | `/api/batch` | 创建批量合成任务（multipart：`file` 或 `input`；也支持 JSON），返回 `task_id` 与句子清单 |
| GET | `/api/batch/tasks/{task_id}` | 批量任务进度 |
| GET | `/api/batch/tasks/{task_id}/download` | 下载结果 ZIP |
| DELETE | `/api/batch/tasks/{task_id}` | 删除任务与临时文件 |

## 合成

`POST /v1/audio/speech` 请求体：

```json
{
  "input": "要合成的文本",
  "voice": "default",
  "ref_audio": "本地路径 / base64 / data URI（可选）",
  "ref_text": "参考音频的准确文本（使用 ref_audio 时必填）",
  "voice_id": "已保存音色 ID（可选）",
  "temperature": 0.7,
  "top_p": 0.9,
  "top_k": 50,
  "response_format": "wav"
}
```

`response_format` 支持 `wav` / `flac` / `ogg` / `mp3`（mp3 编码不可用时自动回退 wav）。`voice_id` 与 `ref_audio` / `ref_text` 互斥：使用 `voice_id` 时参考音频与文本取自音色库记录。

`POST /v1/audio/speech/clone`（multipart）字段：`input`、`ref_audio`（音频文件）、`ref_text`、`voice_id`（可选，替代上传参考音频）、`temperature`、`top_p`、`top_k`、`response_format`。

`POST /api/models/install` 请求体：

```json
{"model": "realistic", "source": "modelscope"}
```

`source` 省略时使用模型默认源；也支持任意 `owner/name` 仓库 ID（默认 HF）。

## 批量合成

`POST /api/batch`（multipart 或 JSON）。

multipart 字段：`file`（`.txt` 上传）或 `input`（文本）、`filename`、`temperature`、`top_p`、`top_k`、`response_format`；可选 `ref_audio`（音频文件）与 `ref_text`（参考音频所说的准确文字），二者需同时提供；也可用 `voice`（已保存音色 ID）直接引用音色库，与 `ref_audio` / `ref_text` 互斥。

JSON 请求体：

```json
{
  "input": "第一句。第二句！",
  "filename": "novel.txt",
  "temperature": 0.7,
  "top_p": 0.9,
  "top_k": 50,
  "response_format": "wav",
  "ref_audio": "参考音频的本地路径 / base64 / data URI（可选）",
  "ref_text": "参考音频所说的准确文字（可选，需与 ref_audio 同时提供）",
  "voice": "已保存音色 ID（可选，与 ref_audio / ref_text 互斥）"
}
```

返回 `task_id`、`total` 与拆分后的 `sentences`。完成后 ZIP 内含 `001.wav`、`002.wav` … 与 `manifest.json`：

```json
{
  "source": "novel.txt",
  "count": 2,
  "format": "wav",
  "sample_rate": 44100,
  "model": "mlx-community/Audio8-TTS-Preview-0.6b-bf16",
  "backend": "mlx",
  "reference": { "audio": "reference.wav", "text": "参考音频所说的准确文字" },
  "sentences": [
    { "index": 1, "file": "001.wav", "text": "第一句。" },
    { "index": 2, "file": "002.wav", "text": "第二句！" }
  ]
}
```

批量任务上限：文本 100000 字、500 句；任务与临时 ZIP 保存在单 worker 进程内，服务重启即丢失。批量合成走与单句相同的模型/参数，支持参考音频克隆（`ref_audio` + `ref_text`）或已保存音色（`voice`）。`manifest.json` 的 `reference` 字段记录所用来源：上传参考音频时为 `{"audio": 文件名, "text": 参考文本}`，已保存音色时为 `{"voice": 音色ID, "name": 音色名, "text": 参考文本}`，未使用克隆时为 `null`。
