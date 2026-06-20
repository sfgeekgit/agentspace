# Modules (reserved)

Optional, toggleable add-ons applied at world-build time (e.g. a "Vegas Room").
**None exist yet** — this directory is reserved so the feature is wired end to
end and can't be forgotten.

- A module is a directory `modules/<name>/`.
- The module registry (`agentspace/registry.py`) scans this directory; today it
  returns an empty list.
- The New-World wizard always shows a **Modules** step driven by that list. With
  zero modules it shows "none available" and continues — proving the path works
  before any module is built.

**Compatibility is bilateral:** a scen may list incompatible modules in its
`module_blacklist`; a module may later declare a scen blacklist of its own. The
builder checks the union before building.
