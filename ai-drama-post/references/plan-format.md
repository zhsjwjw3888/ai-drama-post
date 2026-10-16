# plan.json 与命令

所有媒体路径相对于 plan.json 所在目录，禁止绝对路径、`..` 或符号链接逃出项目。导出使用唯一版本文件名，已存在时拒绝覆盖。

```json
{
  "version": 1,
  "output": "renders/episode-v1.mp4",
  "width": 720,
  "height": 1280,
  "fps": 24,
  "codec": "h264",
  "clips": [{"id": "shot01", "path": "media/shot01.mp4", "start": 0, "duration": 3}],
  "mix": "media/mix.wav",
  "subtitles": "media/captions.ass"
}
```

`mix` 和 `subtitles` 可省略。无 mix 时采用各镜头原声；缺声镜头补静音。镜头入点与时长均为秒，顺序拼接；没有隐式拉伸、冻结或变速。宽高须为正偶数，fps 1–120，codec 为 h264/h265，输出仅 mp4。真实插帧、超分、配音、特效须先由相应工具完成，不能依赖基础导出器自动生成。

```sh
python scripts/studio.py check PROJECT/plan.json
python scripts/studio.py review PROJECT/plan.json --clip shot01 --checks visual audio timing --evidence "reviews/shot01.md：实际播放并检查0至3秒，无脸形漂移，声音和口型一致"
python scripts/studio.py gate PROJECT/plan.json
python scripts/studio.py render PROJECT/plan.json
python scripts/studio.py verify PROJECT/renders/episode-v1.mp4
```

`review` 是助手/人的审核声明，不是自动质量判定；只记录实际执行的检查，局部检查可以分次追加，同一文件变更后旧检查作废。每段都要求 visual/audio/timing。全片的字幕、混音及衔接仍须在导出后审阅。脚本输出验证报告不等于艺术验收。

`doctor` 不改变环境；`queue` 只读且限本机 URL。`render` 会重新 probe 输入、检查时长、核对指纹、生成临时拼接文件、合成混音/字幕、完整解码；异常保留 `.partial.mp4`，不会把它改名为成片。导出工具在执行本地媒体解码，仍应使用可信 FFmpeg 构建和来源可靠的素材。
