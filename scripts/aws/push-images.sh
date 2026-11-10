#!/usr/bin/env bash
# Build the arm64 images and push them to ECR (the first deploy, or by hand; CI pushes the API image).
#   scripts/aws/push-images.sh <bundle-version> [api|build|both]
# The API image tag is <git-sha>-<bundle-version>; the build image tag is <git-sha>. Prints both.
source "$(dirname "$0")/_common.sh"
need docker
version="${1:?usage: push-images.sh <bundle-version> [api|build|both]}"
which="${2:-both}"
sha=$(git -C "$REPO_ROOT" rev-parse --short=12 HEAD)
api_repo=$(out "$FOUNDATION" ApiRepositoryUri)
build_repo=$(out "$FOUNDATION" BuildRepositoryUri)
aws ecr get-login-password | docker login --username AWS --password-stdin "${api_repo%%/*}"
cd "$REPO_ROOT"
if [ "$which" != build ]; then
  work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
  bucket=$(out "$FOUNDATION" DataBucket)
  aws s3 cp "s3://${bucket}/bundles/${version}/facts-${version}.sqlite" "$work/facts.sqlite"
  docker buildx build --platform linux/arm64 --provenance=false --push -f infra/docker/api.Dockerfile \
    --build-context bundle="$work" -t "${api_repo}:${sha}-${version}" .
  echo "API image tag: ${sha}-${version}"
  [ -n "${GITHUB_OUTPUT:-}" ] && echo "api_tag=${sha}-${version}" >> "$GITHUB_OUTPUT"
fi
if [ "$which" != api ]; then
  docker buildx build --platform linux/arm64 --provenance=false --push -f infra/docker/build.Dockerfile \
    -t "${build_repo}:${sha}" -t "${build_repo}:latest" .
  echo "build image tag: ${sha} (also latest)"
fi
