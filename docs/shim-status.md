# shim-status.md

Conformance of `shim/shim.cpp` against the official headers cached in
`docs/official/` (windhawk_api.h + windhawk_api_internal.h, Windhawk v1.7.3
distribution headers; engine-side mods_api.h kept for reference).

Last check: 2026-11-04 13:45 UTC

Signature mismatches: **0 by construction** — the shim is compiled including the
official `windhawk_api_internal.h`, so a wrong signature cannot build.

| InternalWh function | status |
|---|---|
| `InternalWh_ApplyHookOperations` | implemented |
| `InternalWh_ClearDynamicSelectOptions` | stub |
| `InternalWh_DeleteDynamicSelectOption` | stub |
| `InternalWh_DeleteValue` | implemented |
| `InternalWh_Disasm` | stub |
| `InternalWh_FindCloseSymbol` | stub |
| `InternalWh_FindFirstSymbol4` | stub |
| `InternalWh_FindNextSymbol2` | stub |
| `InternalWh_FreeStringSetting` | implemented |
| `InternalWh_FreeUrlContent` | stub |
| `InternalWh_GetBinaryValue` | implemented |
| `InternalWh_GetIntSetting` | implemented |
| `InternalWh_GetIntValue` | implemented |
| `InternalWh_GetModStoragePath` | implemented |
| `InternalWh_GetStringSetting` | implemented |
| `InternalWh_GetStringValue` | implemented |
| `InternalWh_GetUrlContent` | stub |
| `InternalWh_HookSymbols` | stub |
| `InternalWh_IsLogEnabled` | implemented |
| `InternalWh_Log` | implemented |
| `InternalWh_RemoveFunctionHook` | implemented |
| `InternalWh_SetBinaryValue` | implemented |
| `InternalWh_SetDynamicSelectOption` | stub |
| `InternalWh_SetFunctionHook` | implemented |
| `InternalWh_SetIntValue` | implemented |
| `InternalWh_SetStringValue` | implemented |

Public `Wh_*` inline wrappers seen in the official header (21): `Wh_ApplyHookOperations`, `Wh_DeleteValue`, `Wh_Disasm`, `Wh_FindCloseSymbol`, `Wh_FindFirstSymbol`, `Wh_FindNextSymbol`, `Wh_FreeStringSetting`, `Wh_FreeUrlContent`, `Wh_GetBinaryValue`, `Wh_GetIntSetting`, `Wh_GetIntValue`, `Wh_GetModStoragePath`, `Wh_GetStringSetting`, `Wh_GetStringValue`, `Wh_GetUrlContent`, `Wh_Log`, `Wh_RemoveFunctionHook`, `Wh_SetBinaryValue`, `Wh_SetFunctionHook`, `Wh_SetIntValue`, `Wh_SetStringValue`

Behavioural notes (assumed where the docs are silent, see AGENTS.md):
- missing int setting -> 0; missing string setting -> empty string (assumed)
- hook operations queued by `Wh_SetFunctionHook` apply at `Wh_ApplyHookOperations`
  and automatically when `Wh_ModInit` returns (host calls the shim export)
- symbol lookup (`Wh_FindFirstSymbol`, `Wh_HookSymbols`) always fails in the
  sandbox and is reported as `unverifiable: internal-symbols`
