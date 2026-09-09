# shellcheck shell=bash
# XKB scheduled-entry-point helper: make an explicit --env-file authoritative.
#
# Why this exists
# ---------------
# runtime_config.runtime_env() merges the env file *under* os.environ, so the
# process environment always wins. That is the right default for a human typing
# a one-off override, and the wrong one for a scheduler: Hermes starts these
# scripts with its own LLM_API_URL / LLM_API_KEY / LLM_MODEL in the environment,
# which silently replaced the endpoint named in --env-file.
#
# On 2026-09-01 that turned the bookmark worker into a no-op. Every nightly run
# exited "ok" and reported a growing backlog while every single item failed with
# a 404 on a model the inherited endpoint does not serve; the same run started
# by hand, without Hermes in front of it, worked. Eight days, 25 failed items,
# and the daily health summary never mentioned it — it only noticed that the
# search index had not been rewritten.
#
# So: when a caller passes --env-file, that file is the contract for the keys it
# defines. Anything the ambient environment says about those keys is cleared
# before the run, and keys the file does not mention are left alone.
xkb_env_file_wins() {
  local env_file="$1"
  [[ -n "$env_file" && -f "$env_file" ]] || return 0
  local key
  while IFS= read -r key; do
    unset "$key"
  done < <(
    sed -E 's/^[[:space:]]*//; /^#/d; s/^export[[:space:]]+//; /^$/d; s/[[:space:]]*=.*//' "$env_file" \
      | grep -E '^[A-Za-z_][A-Za-z0-9_]*$'
  )
}
