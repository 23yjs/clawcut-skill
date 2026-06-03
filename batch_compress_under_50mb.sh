#!/usr/bin/env bash

set -u

INPUT_DIR="/Users/df/clawcut-skill/data/input"
OUTPUT_DIR="$INPUT_DIR/compressed_under_50mb"

# 硬限制为 50 MiB。
# 实际压缩目标设置为 48 MiB，为 MP4 封装开销预留空间。
MAX_MB=50
TARGET_MB=48
RESERVE_RATIO=0.98

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "错误：没有找到 ffmpeg。"
  echo "macOS 可运行：brew install ffmpeg"
  exit 1
fi

if ! command -v ffprobe >/dev/null 2>&1; then
  echo "错误：没有找到 ffprobe。"
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/batch-compress.XXXXXX")
trap 'rm -rf "$TMP_ROOT"' EXIT

MAX_BYTES=$((MAX_MB * 1024 * 1024))
COUNT=0

find "$INPUT_DIR" \
  -maxdepth 1 \
  -type f \
  -iname '*.mp4' \
  -print0 |
while IFS= read -r -d '' INPUT; do

  COUNT=$((COUNT + 1))

  BASENAME=$(basename "$INPUT")
  STEM="${BASENAME%.*}"

  INPUT_BYTES=$(wc -c < "$INPUT" | tr -d ' ')
  INPUT_MB=$(awk -v bytes="$INPUT_BYTES" \
    'BEGIN { printf "%.2f", bytes / 1024 / 1024 }')

  echo ""
  echo "============================================================"
  echo "处理文件：$BASENAME"
  echo "原始大小：${INPUT_MB} MiB"

  # 已经满足限制的文件不重新编码，避免画质损失
  if [ "$INPUT_BYTES" -le "$MAX_BYTES" ]; then
    cp -p "$INPUT" "$OUTPUT_DIR/$BASENAME"
    echo "无需压缩：文件未超过 ${MAX_MB} MiB，已直接复制。"
    continue
  fi

  DURATION=$(ffprobe \
    -v error \
    -show_entries format=duration \
    -of default=noprint_wrappers=1:nokey=1 \
    "$INPUT")

  if [ -z "$DURATION" ]; then
    echo "跳过：无法读取视频时长。"
    continue
  fi

  # 判断视频是否存在音轨
  HAS_AUDIO=$(ffprobe \
    -v error \
    -select_streams a:0 \
    -show_entries stream=index \
    -of csv=p=0 \
    "$INPUT")

  # 计算目标总码率
  TOTAL_KBPS=$(awk \
    -v size="$TARGET_MB" \
    -v duration="$DURATION" \
    -v reserve="$RESERVE_RATIO" \
    'BEGIN {
      printf "%.0f", size * 8192 * reserve / duration
    }')

  # 根据总码率动态调整音频码率
  if [ -n "$HAS_AUDIO" ]; then
    if [ "$TOTAL_KBPS" -ge 800 ]; then
      AUDIO_KBPS=96
    elif [ "$TOTAL_KBPS" -ge 450 ]; then
      AUDIO_KBPS=64
    else
      AUDIO_KBPS=48
    fi
  else
    AUDIO_KBPS=0
  fi

  VIDEO_KBPS=$((TOTAL_KBPS - AUDIO_KBPS))

  if [ "$VIDEO_KBPS" -lt 120 ]; then
    echo "跳过：目标码率只有 ${VIDEO_KBPS} kbps。"
    echo "原因：视频过长，强行压缩到 ${TARGET_MB} MiB 会导致画质不可用。"
    continue
  fi

  # 在固定体积下，根据可用码率选择合理分辨率
  if [ "$VIDEO_KBPS" -ge 2500 ]; then
    MAX_HEIGHT=1080
  elif [ "$VIDEO_KBPS" -ge 1200 ]; then
    MAX_HEIGHT=720
  elif [ "$VIDEO_KBPS" -ge 700 ]; then
    MAX_HEIGHT=540
  else
    MAX_HEIGHT=480
  fi

  OUTPUT="$OUTPUT_DIR/${STEM}_under_50mb.mp4"
  PASSLOG="$TMP_ROOT/pass_${COUNT}"
  VIDEO_FILTER="scale=-2:min(${MAX_HEIGHT}\,ih)"

  echo "视频时长：${DURATION} 秒"
  echo "视频码率：${VIDEO_KBPS} kbps"
  echo "音频码率：${AUDIO_KBPS} kbps"
  echo "最大高度：${MAX_HEIGHT}p"
  echo "开始第一遍编码：分析画面复杂度..."

  if ! ffmpeg \
    -nostdin \
    -y \
    -hide_banner \
    -loglevel warning \
    -stats \
    -i "$INPUT" \
    -map 0:v:0 \
    -vf "$VIDEO_FILTER" \
    -c:v libx264 \
    -preset medium \
    -b:v "${VIDEO_KBPS}k" \
    -pass 1 \
    -passlogfile "$PASSLOG" \
    -an \
    -f null \
    /dev/null; then

    echo "失败：第一遍编码未完成。"
    continue
  fi

  echo "开始第二遍编码：生成压缩视频..."

  if [ "$AUDIO_KBPS" -gt 0 ]; then
    if ! ffmpeg \
    -nostdin \
    -y \
      -hide_banner \
      -loglevel warning \
      -stats \
      -i "$INPUT" \
      -map 0:v:0 \
      -map 0:a:0? \
      -vf "$VIDEO_FILTER" \
      -c:v libx264 \
      -preset medium \
      -b:v "${VIDEO_KBPS}k" \
      -pass 2 \
      -passlogfile "$PASSLOG" \
      -c:a aac \
      -b:a "${AUDIO_KBPS}k" \
      -pix_fmt yuv420p \
      -movflags +faststart \
      "$OUTPUT"; then

      echo "失败：第二遍编码未完成。"
      continue
    fi
  else
    if ! ffmpeg \
    -nostdin \
    -y \
      -hide_banner \
      -loglevel warning \
      -stats \
      -i "$INPUT" \
      -map 0:v:0 \
      -vf "$VIDEO_FILTER" \
      -c:v libx264 \
      -preset medium \
      -b:v "${VIDEO_KBPS}k" \
      -pass 2 \
      -passlogfile "$PASSLOG" \
      -an \
      -pix_fmt yuv420p \
      -movflags +faststart \
      "$OUTPUT"; then

      echo "失败：第二遍编码未完成。"
      continue
    fi
  fi

  OUTPUT_BYTES=$(wc -c < "$OUTPUT" | tr -d ' ')
  OUTPUT_MB=$(awk -v bytes="$OUTPUT_BYTES" \
    'BEGIN { printf "%.2f", bytes / 1024 / 1024 }')

  echo "压缩完成：$OUTPUT"
  echo "压缩后大小：${OUTPUT_MB} MiB"

  if [ "$OUTPUT_BYTES" -gt "$MAX_BYTES" ]; then
    echo "警告：输出文件仍然超过 ${MAX_MB} MiB，需要进一步降低目标大小。"
  fi

done

echo ""
echo "============================================================"
echo "批处理完成。"
echo "输出目录：$OUTPUT_DIR"
