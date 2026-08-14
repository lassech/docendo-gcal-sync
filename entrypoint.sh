#!/bin/bash
# Write env vars as a properly quoted shell script so cron can source them
printenv | while IFS='=' read -r key value; do
    printf "export %s='%s'\n" "$key" "${value//\'/\'\\\'\'}"
done > /app/env.sh
exec cron -f
