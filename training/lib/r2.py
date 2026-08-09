#!/usr/bin/env python3
"""R2 store helper (boto3/SigV4). VERIFIED working with these R2 creds on
2026-08-09 (boto3 put OK; rclone copyto got AccessDenied). Used for all critical
state/heartbeat/status writes so they actually persist.
Usage:
  r2.py put  KEY     # read stdin
  r2.py get  KEY
  r2.py ls   PREFIX
  r2.py syncLocalDir LOCALDIR  REMOTE_PREFIX   # upload a dir tree
"""
import sys, os, json
import boto3

EP   = os.environ.get("R2_ENDPOINT","")
AK   = os.environ.get("R2_ACCESS_KEY","")
SK   = os.environ.get("R2_SECRET_KEY","")
BUCK = os.environ.get("R2_BUCKET","")
_BUK_OK = {}

def client():
    boto3.set_stream_logger("", level="ERROR")
    return boto3.client("s3", endpoint_url=EP,
        aws_access_key_id=AK, aws_secret_access_key=SK, region_name="auto")

def put(key):
    data = sys.stdin.buffer.read()
    c = client()
    c.put_object(Bucket=BUCK, Key=key, Body=data)
    print("put", key, len(data))

def get(key):
    c = client()
    r = c.get_object(Bucket=BUCK, Key=key)
    sys.stdout.buffer.write(r["Body"].read())

def ls(prefix):
    c = client()
    r = c.list_objects_v2(Bucket=BUCK, Prefix=prefix)
    for o in r.get("Contents",[]):
        print(o["Key"], o["Size"], o["LastModified"])

def sync_dir(local, remote_prefix):
    c = client()
    n=0
    for root, dirs, files in os.walk(local):
        for fn in files:
            lp = os.path.join(root, fn)
            rel = os.path.relpath(lp, local).replace("\\","/")
            rk = f"{remote_prefix.rstrip('/')}/{rel}" if remote_prefix else rel
            c.upload_file(lp, BUCK, rk)
            n+=1
    print("synced", n, "files ->", remote_prefix)

if __name__ == "__main__":
    action = sys.argv[1]
    if action == "put": put(sys.argv[2])
    elif action == "get": get(sys.argv[2])
    elif action == "ls": ls(sys.argv[2])
    elif action == "syncLocalDir": sync_dir(sys.argv[2], sys.argv[3])
    else: sys.exit("unknown")