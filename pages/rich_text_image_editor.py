from PyQt5.QtCore import QByteArray, QBuffer, QIODevice
from PyQt5.QtGui import QTextCursor, QTextDocument, QTextImageFormat
from PyQt5.QtWidgets import QInputDialog, QTextEdit


def looks_like_html(value: str) -> bool:
    text = (value or "").strip().lower()
    return "<img" in text or "<html" in text or "<p" in text or "<br" in text or "<div" in text


def html_to_plain_text(value: str) -> str:
    if not looks_like_html(value):
        return value or ""
    doc = QTextDocument()
    doc.setHtml(value or "")
    return doc.toPlainText()


class RichTextImageEditor(QTextEdit):
    """QTextEdit that can paste screenshots as inline, resizable images."""

    def set_content(self, value: str):
        if looks_like_html(value):
            self.setHtml(value or "")
        else:
            self.setPlainText(value or "")

    def content(self) -> str:
        html = self.toHtml()
        if "<img" in html.lower():
            return html
        return self.toPlainText().strip()

    def has_content(self) -> bool:
        if self.toPlainText().strip():
            return True
        return "<img" in self.toHtml().lower()

    def canInsertFromMimeData(self, source):
        return source.hasImage() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasImage():
            image = source.imageData()
            if image:
                self._insert_image(image)
                return
        super().insertFromMimeData(source)

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        resize_action = menu.addAction("Resize image")
        resize_action.triggered.connect(lambda: self.resize_image_at_cursor())
        menu.exec_(event.globalPos())

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        self.resize_image_at_cursor()

    def _insert_image(self, image):
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        encoded = bytes(byte_array.toBase64()).decode("ascii")
        width = min(max(240, int(image.width())), 900)
        width, ok = QInputDialog.getInt(
            self,
            "Image Width",
            "Width in pixels:",
            width,
            80,
            2000,
            10,
        )
        if not ok:
            width = min(max(240, int(image.width())), 900)

        fmt = QTextImageFormat()
        fmt.setName(f"data:image/png;base64,{encoded}")
        fmt.setWidth(width)
        self.textCursor().insertImage(fmt)

    def resize_image_at_cursor(self):
        cursor = self._image_cursor_near_cursor()
        if not cursor:
            return
        fmt = cursor.charFormat().toImageFormat()
        current_width = int(fmt.width() or 500)
        width, ok = QInputDialog.getInt(
            self,
            "Resize Image",
            "Width in pixels:",
            current_width,
            80,
            2000,
            10,
        )
        if not ok:
            return
        fmt.setWidth(width)
        cursor.setCharFormat(fmt)

    def _image_cursor_near_cursor(self):
        base = self.textCursor()
        for move in (None, QTextCursor.PreviousCharacter, QTextCursor.NextCharacter):
            cursor = QTextCursor(base)
            if move is not None:
                cursor.movePosition(move, QTextCursor.KeepAnchor)
            fmt = cursor.charFormat()
            if fmt.isImageFormat():
                if not cursor.hasSelection():
                    cursor.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
                return cursor
        return None
