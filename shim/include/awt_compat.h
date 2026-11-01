// Sandbox compatibility shim: MSVC exposes ::nullptr_t globally; libstdc++/mingw
// only provide std::nullptr_t. The official windhawk_utils.h uses the unqualified
// name, so we provide the global alias before any official header is parsed.
#pragma once
#include <cstddef>
#if !defined(_MSC_VER) && !defined(AWT_NULLPTR_T_DEFINED)
#define AWT_NULLPTR_T_DEFINED
typedef decltype(nullptr) nullptr_t;
#endif
