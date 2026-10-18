# Personal BOM columns — Nexus 4.0.8

Each signed-in user can save a personal column layout:

1. Resize columns or drag their headers into the desired order.
2. Open **Columns**, or right-click a column header.
3. Select the columns to display.
4. Click **Save for Me**.

The saved visibility, widths, and order are restored in later sessions. CAD and
EBOM keep separate layouts; search and filter views use their corresponding
saved layout. The primary CAD name column always remains visible.

Preferences are stored with the app user in the workspace database, so saving
one user's layout does not overwrite another user's choices. Existing local
visibility choices serve as initial defaults until a personal layout is saved.
Cancel leaves the saved layout unchanged. If a future update changes the column
schema, incompatible header geometry is ignored while valid visibility choices
are retained.


## Development State and Item Status

**Development State** is the automatic state of the native work shared in master:
Under development, Validated, or Not recorded when there is no recorded master
submission. **Item Status** is a dropdown with Design, Released, and Obsolete.
Released rows have a green background and a RELEASED badge beside their name;
Obsolete rows use strikethrough text. Existing custom status values are preserved
until explicitly changed. It is available as a separate optional column and is
labelled Item Status in the Item editor. Changing one does not imply the other.

Restored layouts compact hidden columns instead of reserving blank space. Layouts
saved before the Development State rename retain their existing widths and order.
