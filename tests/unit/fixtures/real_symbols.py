"""Decorated-name fixtures taken from real Windows shell modules.

Provenance and why this is here
-------------------------------
Every string in this file is a *compiler-generated symbol name* of the kind
Microsoft's public PDBs contain and the kind Windhawk mods put in their
`SYMBOL_HOOK` tables. They were collected while validating this bench's
extractor and demangler against the 860 mods published in
`ramensoftware/windhawk-mods` (checked out at commit
`de69b332c19effb929b5297281f61044930001e7`, 2026-11-04) and against the symbol
names those mods request. That repository has no LICENSE file, so nothing but
these short machine-generated identifiers is reproduced here - no mod source
text, no Microsoft binary, no PDB content. Symbol names are not copyrightable
expression; they are the interface this feature has to speak.

The expectations next to each name were checked by hand against the MSVC
decoration rules (qualified name innermost-first, `XZ` = empty parameter list,
static member functions do carry a return type, `PEAD` = `char *__ptr64`).

Validation result on that corpus, for the record: 375 distinct decorated names
extracted from the 860 mods, 357 decoded with confidence `structural` and 18
with confidence `partial` (Chromium-style encodings: `__Cr@std`, `_SU`,
`_N2@Z`). `partial` is a safe degradation - the comparison then uses the
qualified name only and says so.
"""

# (decorated name, qualified name, parameter count, confidence)
DECORATED = [
    ("?Invoke@CTaskFrame@@UEAAJXZ",
     "CTaskFrame::Invoke", 0, "structural"),
    ("?WndProc@CTaskFrame@@MEAA_JPEAUHWND__@@I_K_J@Z",
     "CTaskFrame::WndProc", 4, "structural"),
    ("?wWinMain@@YAHPEAUHINSTANCE__@@HPEADH@Z",
     "wWinMain", 4, "structural"),
    ("?SetWindowPos@@YAHPEAUHWND__@@0HHHHH@Z",
     "SetWindowPos", 7, "structural"),
    ("?foo@Bar@@QEAAHPEAUtagRECT@@H@Z",
     "Bar::foo", 2, "structural"),
    ("?Create@Factory@@SA?AVComPtr@IUnknown@@XZ",
     "Factory::Create", 0, "structural"),
    ("?ShowStartView@XamlLauncher@@UEAAJW4IMMERSIVELAUNCHERSHOWMETHOD@@"
     "W4IMMERSIVELAUNCHERSHOWFLAGS@@@Z",
     "XamlLauncher::ShowStartView", 2, "structural"),
    ("?assign@?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@"
     "QEAAAEAV12@PEBDH@Z",
     "std::basic_string::assign", 2, "structural"),
    ("?operator new@@YAPEAX_K@Z",
     "operator new", 1, "structural"),
    ("??1?$ScopedComPtr@IUnknown@@@@QEAA@XZ",
     "ScopedComPtr::~ScopedComPtr", 0, "structural"),
    # `anonymous namespace' is written with backquotes by MSVC and by DIA alike.
    ("?StaticHelper@`anonymous namespace'@@YAXXZ",
     "`anonymous namespace'::StaticHelper", 0, "structural"),
]

# Constructors repeat the class name: `??0AppMenu@@...` is `AppMenu::AppMenu`,
# and they carry no return type in the encoding.
CONSTRUCTORS = [
    ("??0AppMenu@@QEAA@XZ", "AppMenu::AppMenu", 0),
    ("??0BookmarkBarView@@QEAA@PEAVBrowser@@@Z", "BookmarkBarView::BookmarkBarView", 1),
]

# The compiler-generated deleting destructors. Unlike `??0` / `??1` they do have
# a return type (`void *`) and one `unsigned int` parameter.
DELETING_DESTRUCTORS = [
    ("??_GBookmarkBarView@@UEAAPEAXI@Z",
     "BookmarkBarView::`scalar deleting destructor'", 1),
    ("??_EBookmarkBarView@@UEAAPEAXI@Z",
     "BookmarkBarView::`vector deleting destructor'", 1),
]

# (decorated name, qualified name, kind)
DATA_SYMBOLS = [
    ("?s_sharedState@XamlLauncher@@0PEAVSharedState@@EB",
     "XamlLauncher::s_sharedState", "data"),
    ("?g_taskbarCount@@3HA", "g_taskbarCount", "data"),
    ("?g_pTaskbar@@3PEAVCTaskbar@@EA", "g_pTaskbar", "data"),
    ("??_7CImmersiveMenu@@6B@", "CImmersiveMenu", "data"),   # `vftable'
]

# (undecorated text as DIA prints it, decorated name in the PDB) - must match.
UNDDECORATED_PAIRS = [
    ("public: virtual long __cdecl CTaskFrame::Invoke(void)",
     "?Invoke@CTaskFrame@@UEAAJXZ"),
    ("int __cdecl wWinMain(struct HINSTANCE__ * __ptr64,int,char * __ptr64,int)",
     "?wWinMain@@YAHPEAUHINSTANCE__@@HPEADH@Z"),
    # `MEAA` is *protected* virtual: DIA prints the access specifier the
    # decoration encodes, and getting it wrong here would hide a real mismatch.
    ("protected: virtual __int64 __cdecl CTaskFrame::WndProc(struct HWND__ * __ptr64,"
     "unsigned int,unsigned __int64,long)",
     "?WndProc@CTaskFrame@@MEAA_JPEAUHWND__@@I_K_J@Z"),
    ("bool __cdecl ImmersiveContextMenuHelper::CanApplyOwnerDrawToMenu("
     "struct HMENU__ *,struct HWND__ *)",
     None),      # no decorated counterpart: exercised by the extractor tests
]

# Pairs that must NOT match: same member name, different class.
NON_MATCHING = [
    ("public: void __cdecl Bar::foo(struct tagRECT *,int)",
     "?foo@Baz@@QEAAHH@Z"),
    ("public: virtual long __cdecl CTaskFrame::Invoke(void)",
     "?WndProc@CTaskFrame@@MEAA_JPEAUHWND__@@I_K_J@Z"),
]

# Same qualified name, different signature: a different overload. The verdict is
# a name-only match with confidence `partial` and an explicit note, never a
# silent `found`, and never `missing` either (the name IS in the PDB).
OVERLOAD_PAIRS = [
    ("public: void __cdecl C::f(int,int)", "?f@C@@QEAAXH@Z"),
]

# 32-bit decorations: complete names, compared byte for byte (no decoding).
X86_NAMES = [
    "_CKbd_GetDeviceState@8",
    "@Init@12",
]

# Names this demangler is expected to decode only partially. The requirement is
# that the qualified name is still right and that the verdict says `partial`
# instead of pretending to a structural comparison.
PARTIAL_DECORATED = [
    ("?CalculateDisplayName@AccountInfo@Msoa@@AEBA?AV?$basic_string@DU?"
     "$char_traits@D@std@@V?$allocator@D@2@@std@@@Z",
     "Msoa::AccountInfo::CalculateDisplayName"),
]
