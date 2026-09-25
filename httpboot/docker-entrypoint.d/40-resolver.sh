#!/bin/sh
# Writes the container's own DNS resolver (Podman's aardvark-dns or Docker's
# embedded DNS, from /etc/resolv.conf) into an nginx "resolver" directive, so
# nginx.conf can `include` it instead of hardcoding a subnet-specific IP that
# would only work on this one network.
set -eu

ns="$(awk '/^nameserver/ { print $2; exit }' /etc/resolv.conf)"
if [ -z "$ns" ]; then
    echo "40-resolver.sh: no nameserver found in /etc/resolv.conf, skipping" >&2
    exit 0
fi
echo "resolver $ns valid=10s;" > /tmp/resolver.conf
