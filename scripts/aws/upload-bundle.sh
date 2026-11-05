#!/usr/bin/env bash
# Upload a facts bundle built locally (and the embedding cache) to the data bucket, in the layout the build
# image writes (docs/aws/RUNBOOK.md section 3).
#   scripts/aws/upload-bundle.sh bundles/facts-2027.2.sqlite [bundles/embeddings.sqlite]
source "$(dirname "$0")/_common.sh"
bundle="${1:?usage: upload-bundle.sh <facts-<version>.sqlite> [embeddings.sqlite]}"
name=$(basename "$bundle"); version="${name#facts-}"; version="${version%.sqlite}"
[ "$name" = "facts-${version}.sqlite" ] || { echo "expected a file named facts-<version>.sqlite" >&2; exit 2; }
bucket=$(out "$FOUNDATION" DataBucket)
sha() { python3 -c "import hashlib,sys;h=hashlib.sha256();f=open(sys.argv[1],'rb')
while b:=f.read(1<<23): h.update(b)
print(h.hexdigest())" "$1"; }
aws s3 cp "$bundle" "s3://${bucket}/bundles/${version}/${name}" --metadata "sha256=$(sha "$bundle")" --sse AES256
if [ -n "${2:-}" ]; then
  aws s3 cp "$2" "s3://${bucket}/bundles/cache/embeddings.sqlite" --metadata "sha256=$(sha "$2")" --sse AES256
fi
echo "bundle ${version}: s3://${bucket}/bundles/${version}/${name}"
