package it.mcllo.plexinventory

import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

object XlsxWriter {
    fun write(file: File, headers: List<String>, rows: List<List<String>>) {
        file.parentFile?.mkdirs()
        file.outputStream().use { out ->
            ZipOutputStream(out).use { zip ->
                zip.text("[Content_Types].xml", contentTypes())
                zip.text("_rels/.rels", rootRels())
                zip.text("xl/workbook.xml", workbook())
                zip.text("xl/_rels/workbook.xml.rels", workbookRels())
                zip.text("xl/styles.xml", styles())
                zip.text("xl/worksheets/sheet1.xml", sheet(headers, rows))
            }
        }
    }

    private fun ZipOutputStream.text(path: String, value: String) {
        putNextEntry(ZipEntry(path))
        write(value.toByteArray(Charsets.UTF_8))
        closeEntry()
    }

    private fun contentTypes() = """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Default Extension="xml" ContentType="application/xml"/>
          <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
          <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
          <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
        </Types>
    """.trimIndent()

    private fun rootRels() = """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
        </Relationships>
    """.trimIndent()

    private fun workbook() = """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
          <sheets><sheet name="Plex Inventory" sheetId="1" r:id="rId1"/></sheets>
        </workbook>
    """.trimIndent()

    private fun workbookRels() = """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
          <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
        </Relationships>
    """.trimIndent()

    private fun styles() = """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
          <fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
          <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
          <borders count="1"><border/></borders>
          <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
          <cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/></cellXfs>
        </styleSheet>
    """.trimIndent()

    private fun sheet(headers: List<String>, rows: List<List<String>>): String {
        val all = listOf(headers) + rows
        return buildString {
            append("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>")
            append("<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>")
            all.forEachIndexed { rIdx, row ->
                val rowNum = rIdx + 1
                append("<row r=\"").append(rowNum).append("\">")
                row.forEachIndexed { cIdx, value ->
                    val ref = col(cIdx) + rowNum
                    val style = if (rIdx == 0) " s=\"1\"" else ""
                    append("<c r=\"").append(ref).append("\" t=\"inlineStr\"").append(style).append("><is><t>")
                    append(escape(value))
                    append("</t></is></c>")
                }
                append("</row>")
            }
            append("</sheetData></worksheet>")
        }
    }

    private fun col(index: Int): String {
        var n = index + 1
        val out = StringBuilder()
        while (n > 0) {
            val rem = (n - 1) % 26
            out.insert(0, ('A'.code + rem).toChar())
            n = (n - 1) / 26
        }
        return out.toString()
    }

    private fun escape(s: String): String = s
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\"", "&quot;")
        .replace("'", "&apos;")
}
