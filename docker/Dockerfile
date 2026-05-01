# One image, four Lambda entry points. Each Lambda's
# `image_config.commands` picks the right handler at deploy time.
#
# Base: AWS public ECR Lambda Python 3.12. boto3 is preinstalled, so
# we don't reinstall it from requirements.txt — the file installs the
# heavy native deps (numpy via beatwav, pydub) and our two private
# git deps (beatwav, octapy).
FROM public.ecr.aws/lambda/python:3.12

# git is needed to install beatwav / octapy from their git URLs in
# requirements.txt. yum-clean keeps the layer lean.
RUN dnf install -y git && dnf clean all

WORKDIR ${LAMBDA_TASK_ROOT}

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# One-shot drum samples baked into the image at /opt/oneshots/. The
# buildspec syncs s3://wol-samplebank/samples/ into ./oneshots/ before
# `docker build`; sample_source.py prefers /opt/oneshots when present
# and falls back to a runtime boto3 sync only for local dev where the
# bake step doesn't run. Lambda's read-only layer makes this immutable
# at runtime — exactly what we want.
COPY oneshots /opt/oneshots

# Default CMD is overridden per-Lambda by `image_config.commands`.
# Pointing at the strudel handler here just gives docker-run a
# sensible default for local sanity checks.
CMD ["app.api.strudel.handler.handler"]
