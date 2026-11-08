FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

# The base image's Yarn apt repo has an expired GPG key (NO_PUBKEY 62D54FD4003F6525) -> `apt-get update` fails
# and the docker-in-docker feature fails to install. The project does not use Yarn, so drop that repo.
RUN rm -f /etc/apt/sources.list.d/yarn.list
