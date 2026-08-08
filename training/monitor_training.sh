#!/bin/bash
# Training monitor: checks R2 for training artifacts (checkpoints, startup logs,
# pipeline state) and prints a compact status. Silent if nothing new/changed
# (watchdog pattern: only report on change or completion or error).
# Run by a scheduled cron/hermes job.
set -u

KEY="3c3409aa4d7d489b4d01fb10e92aba06"
SECRET="4860352c0ad6b9248638c4adf4681e537afab2283ff2f15e7b23fb85c1bec2d3"
EP="https://abd12cd58366e2d99a202218328b1340.r2.cloudflarestorage.com"
BUCKET="pcgenius-build"
STATE="state/training_monitor_state.txt"

# fetch current snapshot (use `python`, not `python3`, on this Windows host)
export R2_EP="$EP" R2_KEY="$KEY" R2_SECRET="$SECRET" R2_BUCKET="$BUCKET"
python - <<'PY' > /tmp/train_mon.txt 2>&1
import boto3, json, os
s3=boto3.client("s3", endpoint_url=os.environ["R2_EP"],
    aws_access_key_id=os.environ["R2_KEY"], aws_secret_access_key=os.environ["R2_SECRET"])
try:
    r=s3.list_objects_v2(Bucket=os.environ["R2_BUCKET"], Prefix="artifacts/checkpoints/")
    ck=[o["Key"] for o in r.get("Contents",[])]
except Exception as e:
    ck=["ERR:"+str(e)[:60]]
try:
    r2=s3.list_objects_v2(Bucket=os.environ["R2_BUCKET"], Prefix="logs/")
    lg=[o["Key"] for o in r2.get("Contents",[])]
except Exception as e:
    lg=[]
try:
    st=s3.get_object(Bucket=os.environ["R2_BUCKET"], Key="state/pipeline_state.txt")["Body"].read().decode().strip()
except Exception:
    st="?"
print(json.dumps({"checkpoints":ck,"logs":lg,"stage":st}, default=str))
PY

SNAP=$(cat /tmp/train_mon.txt)
# read last snapshot
LAST=""
if command -v rclone >/dev/null 2>&1; then
  LAST=$(rclone cat "r2:$BUCKET/$STATE" 2>/dev/null || echo "")
fi

if [ "$SNAP" != "$LAST" ] || [ "$1" = "force" ]; then
  echo "Training status: $SNAP"
  # persist
  if command -v rclone >/dev/null 2>&1; then
    echo "$SNAP" | rclone rcat "r2:$BUCKET/$STATE" 2>/dev/null || true
  fi
fi
