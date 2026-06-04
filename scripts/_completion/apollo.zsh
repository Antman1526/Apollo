#compdef apollo apollo-backup apollo-calendar apollo-contacts apollo-cookbook apollo-docs apollo-gallery apollo-mail apollo-mcp apollo-memory apollo-notes apollo-personal apollo-preset apollo-research apollo-sessions apollo-signature apollo-skills apollo-tasks apollo-theme apollo-webhook
# Zsh tab-completion for the apollo umbrella + sub-CLIs.
#
# Drop in any directory on $fpath, e.g.:
#     fpath=(/path/to/apollo-ui/scripts/_completion $fpath)
#     autoload -U compinit; compinit
#
# Then `apollo <tab>` completes subcommands; `apollo mail <tab>`
# completes mail subcommands; `apollo-mail <tab>` works the same.

_apollo_scripts_dir() {
    local self="${(%):-%x}"
    while [[ -L "$self" ]]; do self="$(readlink "$self")"; done
    cd "${self:h}/.." && pwd
}

typeset -gA _apollo_subs

_apollo_refresh() {
    _apollo_subs=()
    local dir="$(_apollo_scripts_dir)"
    local py="$dir/../venv/bin/python"
    [[ -x "$py" ]] || py="$(command -v python3)"
    local f sub help_out commands
    for f in "$dir"/apollo-*; do
        [[ -x "$f" ]] || continue
        case "$f" in
            *.bak|*.pyc|*.pre-*) continue ;;
        esac
        sub="${${f:t}#apollo-}"
        help_out=$("$py" "$f" --help 2>/dev/null) || continue
        commands=$(echo "$help_out" | grep -oE '\{[a-z0-9_,-]+\}' | head -1 \
            | tr -d '{}' | tr ',' ' ')
        _apollo_subs[$sub]="$commands"
    done
}

_apollo() {
    [[ ${#_apollo_subs} -eq 0 ]] && _apollo_refresh

    local cmd="${words[1]}"

    if [[ "$cmd" == "apollo" ]]; then
        if (( CURRENT == 2 )); then
            local -a subs=(${(k)_apollo_subs} help)
            _describe 'subcommand' subs
            return
        fi
        local sub="${words[2]}"
        if [[ "$sub" == "help" ]] && (( CURRENT == 3 )); then
            local -a subs=(${(k)_apollo_subs})
            _describe 'subcommand' subs
            return
        fi
        if (( CURRENT == 3 )); then
            local -a sc=(${(s/ /)_apollo_subs[$sub]})
            _describe 'command' sc
            return
        fi
        return
    fi

    # apollo-foo <tab>
    local sub="${cmd#apollo-}"
    if (( CURRENT == 2 )); then
        local -a sc=(${(s/ /)_apollo_subs[$sub]})
        _describe 'command' sc
        return
    fi
}

_apollo "$@"
