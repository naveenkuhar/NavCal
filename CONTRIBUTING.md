# Contributing to Navcal

Navcal is a GNOME calendar app: Python + GTK 4 + libadwaita, with events stored in Evolution Data
Server. Its style reference is GNOME Calendar. See README.md for setup, layout and tests.

- Unit tests: `uv run pytest`. Calendar-service tests: `tests/run-eds-tests.sh` (they run in a
  private, throwaway D-Bus session, so they never touch your own calendars).
- UI definitions (`src/navcal/ui/*.ui`) and custom icons are compiled into a GResource:
  run `tools/build-resources.sh` after changing anything under `src/navcal/ui/` or `src/navcal/icons/`.
- All user-visible text is translatable: wrap it in `_()` / `ngettext()` from `navcal.i18n` (full
  sentences with named `{placeholders}`, never text glued together), mark .ui text
  `translatable="yes"`, never use `_` as a throwaway variable, and run `tools/update-translations.sh`.

# UI Rules: GNOME Human Interface Guidelines

This is a GNOME app. Every UI change follows the GNOME HIG (https://developer.gnome.org/hig/)
using **GTK 4 + libadwaita**. These rules are not suggestions: if a change would break one,
discuss it first.

After a UI change, go through the "Review checklist" at the bottom and mention which items you
checked.

## Toolkit
- Use GTK 4 and libadwaita (`Adw.*`). Never GTK 3. Never hand-roll a widget libadwaita already provides.
- Main window: `Adw.ApplicationWindow` (or `Adw.Window`), app class: `Adw.Application`.
- Content under a header bar goes in `Adw.ToolbarView` with an `Adw.HeaderBar` as top bar.
- Prefer Blueprint (`.blp`) or GtkBuilder `.ui` files for layout over building big UIs in code.
- Don't write custom CSS for colors, fonts, or spacing unless there's no style class for it.
  Use libadwaita style classes: `suggested-action`, `destructive-action`, `flat`, `pill`,
  `circular`, `boxed-list`, `card`, `title-1`…`title-4`, `heading`, `caption`, `dim-label`,
  `numeric`, `toolbar`, `navigation-sidebar`, `accent`, `success`, `warning`, `error`.
- Never hardcode colors. Use named colors / CSS variables (`var(--accent-bg-color)`, etc.)
  so light mode, dark mode, high contrast and accent colors all work.

## Windows & header bars
- No traditional menu bar (File/Edit/View). No title bar + separate toolbar. Use a header bar.
- Header bar layout: primary action(s) on the left, main menu (`open-menu-symbolic`, "Main Menu")
  on the far right, before window controls.
- The main menu holds app-level items only: Preferences, Keyboard Shortcuts, About <App>.
  Nothing else unless there's a strong reason. No "Quit" item (Ctrl+Q handles it).
- Header bar buttons are icon-only (symbolic icons) with tooltips. Text buttons only for
  things like "Cancel"/"Done" in dialogs or selection mode.
- Keep header bars sparse: ~3 controls per side max. Overflow goes in a menu.
- Use `Adw.WindowTitle` for title + subtitle.

## Navigation & layout
- Sidebar + content: `Adw.NavigationSplitView` (or `Adw.OverlaySplitView` for utility panes).
- Drill-down pages: `Adw.NavigationView` + `Adw.NavigationPage` (back button is automatic).
- 2–5 top-level views: `Adw.ViewStack` + `Adw.ViewSwitcher` in the header bar, and an
  `Adw.ViewSwitcherBar` at the bottom on narrow widths.
- Tabs (documents/browser style): `Adw.TabView` + `Adw.TabBar` / `Adw.TabOverview`.
- Settings-style content: `Adw.PreferencesPage` / `Adw.PreferencesGroup`, or lists with the
  `boxed-list` style class, inside `Adw.Clamp` (content max width ~600–800px).
- Rows: use `Adw.ActionRow`, `Adw.SwitchRow`, `Adw.EntryRow`, `Adw.PasswordEntryRow`,
  `Adw.ComboRow`, `Adw.SpinRow`, `Adw.ExpanderRow`, `Adw.ButtonRow` — don't build rows from raw boxes.
- Search: `Gtk.SearchBar` + `Gtk.SearchEntry`, toggled by a search button and Ctrl+F; typing
  in the main view should start searching.

## Adaptiveness (must work on small screens)
- The app must be usable down to **360×294 px**. Test narrow widths.
- Use `Adw.Breakpoint` to collapse sidebars, move the view switcher to the bottom, etc.
- Never set fixed widths that force horizontal scrolling. Use `Adw.Clamp` for readable widths.

## Controls
- Settings that apply instantly: `Adw.SwitchRow` / `Gtk.Switch`. No "Apply" buttons for these.
- Checkboxes only for multi-select lists or "agree"-style options; radio buttons for 2–4
  mutually exclusive choices; `Adw.ComboRow`/drop-down for more.
- Only ONE `suggested-action` (blue) button per view. `destructive-action` (red) only for
  actions that lose data.
- Button labels are verbs describing the action: "Delete", "Save", "Discard" — not "OK"/"Yes"/"No".

## Dialogs & feedback
- Dialogs: `Adw.Dialog`; confirmations/alerts: `Adw.AlertDialog`; preferences:
  `Adw.PreferencesDialog`; about: `Adw.AboutDialog`; shortcuts: `Adw.ShortcutsDialog`
  (or GtkShortcutsWindow on older libadwaita).
- Avoid dialogs when possible. Don't confirm reversible actions — do them and offer **Undo**
  in an `Adw.Toast` (via `Adw.ToastOverlay`).
- Alert dialog: heading is a short question/statement ("Delete Project?"), body explains
  the consequence, responses are verbs ("Cancel" / "Delete"), destructive one styled
  `ADW_RESPONSE_DESTRUCTIVE`.
- Persistent state messages (offline, read-only): `Adw.Banner`.
- Empty/error/first-run states: `Adw.StatusPage` with a symbolic icon, title, description,
  and an action button if there's something to do.
- Loading: `Adw.Spinner` (or `Adw.SpinnerPaintable`) for unknown duration, `Gtk.ProgressBar`
  when progress is known. Don't block the UI thread.
- Tooltips on every icon-only button. Tooltips are short and don't end with a period.

## Writing style
- **Header capitalization** (Title Case) for: window titles, header bar titles, button labels,
  menu items, tab names, group/page titles. e.g. "Keyboard Shortcuts", "Open File".
- **Sentence capitalization** for: descriptions, subtitles, row labels in prefs, tooltips,
  checkbox/switch labels, body text, placeholders. e.g. "Show hidden files".
- Use "…" (ellipsis U+2026, not "...") on menu items/buttons that need more input before acting:
  "Open…", "Save As…".
- Plain, short, friendly. No jargon, no blaming the user, no "Please". No exclamation marks
  except rare positive moments. Say what happened and what to do next.
- Use real typographic characters: “quotes”, ’ apostrophe, – en dash, × multiplication.

## Icons
- UI icons are symbolic (`*-symbolic`) from the icon theme. Don't bundle PNGs for UI buttons.
- Use standard icon names (`list-add-symbolic`, `edit-delete-symbolic`, `open-menu-symbolic`,
  `system-search-symbolic`, `go-previous-symbolic`, etc.). Custom symbolic icons go in the
  app's GResource under `icons/scalable/actions/`.
- App icon: full-color, follows GNOME app icon style, plus a symbolic variant.

## Keyboard & accessibility
- Everything must work with the keyboard alone. Logical focus order. Visible focus.
- Standard shortcuts (don't reassign them): Ctrl+Q quit, Ctrl+W close window/tab, Ctrl+N new,
  Ctrl+O open, Ctrl+S save, Ctrl+Shift+S save as, Ctrl+F search, Ctrl+Z / Ctrl+Shift+Z undo/redo,
  Ctrl+, preferences, Ctrl+? keyboard shortcuts, F10 main menu, Esc close dialog/cancel.
- Wire shortcuts through `Gio` actions (`app.*` / `win.*`) + `set_accels_for_action`, and list
  them in the shortcuts dialog.
- Mnemonics (underscored letters) on labels/buttons in dialogs and forms.
- Icon-only widgets need an accessible label (tooltip or `accessible-label` property).
- Don't convey meaning by color alone. Respect high contrast and large text.
- Touch targets ≥ 32px (libadwaita defaults already handle this — don't shrink them).

## Spacing & typography
- Use libadwaita default spacing; when you must set it, use multiples of 6px
  (6, 12, 18, 24). Margins around main content are typically 12px or more.
- Use typography style classes (`title-1`, `heading`, `caption`, `dim-label`), never
  hardcoded font sizes or font families.

## Don'ts (common mistakes — do NOT do these)
- ❌ Menu bars, "Help" menus, "Exit" buttons
- ❌ OK/Cancel "Apply" pattern for preferences — changes apply live
- ❌ Hardcoded colors, fonts, font sizes, or custom themes
- ❌ Multiple blue (suggested) buttons in one view
- ❌ Confirmation dialogs for undoable actions
- ❌ Labels on header bar icon buttons (use tooltips instead)
- ❌ Fixed window sizes / layouts that break under 360px wide
- ❌ Raw `Gtk.Box` stacks pretending to be list rows or preference pages
- ❌ GTK 3 APIs, `Gtk.Dialog`, `Gtk.MessageDialog`, `Gtk.FileChooserDialog`
  (use `Gtk.FileDialog`, `Gtk.AlertDialog`/`Adw.AlertDialog`)
- ❌ "..." instead of "…", or Title Case in descriptions

## Review checklist (run after every UI change)
1. Only libadwaita/GTK 4 widgets; no deprecated GTK 3/4 dialogs?
2. Header bar: primary action left, main menu right, icon buttons have tooltips?
3. Works at 360px wide? Breakpoints set where needed?
4. Capitalization: Title Case for titles/buttons/menus, sentence case for everything else?
5. Ellipsis "…" only where more input is needed?
6. At most one suggested-action button; destructive-action only for data loss?
7. No hardcoded colors/fonts/sizes; dark mode and high contrast look right?
8. Fully keyboard-usable, standard shortcuts wired via actions?
9. Empty/error states use `Adw.StatusPage`; undoable actions use a toast with Undo?
10. Texts are short, plain, and blame-free?

If unsure how something should look, check the matching page at https://developer.gnome.org/hig/
and the libadwaita docs at https://gnome.pages.gitlab.gnome.org/libadwaita/doc/ before guessing.
