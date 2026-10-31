FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

# Repo apt cua Yarn trong image goc co khoa GPG het han (NO_PUBKEY 62D54FD4003F6525) -> `apt-get update` loi
# va feature docker-in-docker cai that bai. Du an khong dung Yarn nen bo repo nay.
RUN rm -f /etc/apt/sources.list.d/yarn.list
