/**
 * IM8 Link Sync — Google Apps Script Web App
 * ------------------------------------------------------------------
 * Lets the IM8 Discord bot append shared links to this sheet without a
 * Google Cloud service account. The script runs as the sheet owner, so a
 * public link is not required and your sheet can stay private.
 *
 * Layout written:  A = Discord handle | B = display name | C = link
 * Appends after the last used row (row 1 is left for your header).
 *
 * SETUP (one time)
 *  1. Open the target sheet → Extensions ▸ Apps Script.
 *  2. Delete the sample code, paste this whole file, and change SECRET below
 *     to a long random string.
 *  3. Deploy ▸ New deployment ▸ type "Web app".
 *       - Execute as:    Me
 *       - Who has access: Anyone
 *     Click Deploy, authorize, and copy the "/exec" Web app URL.
 *  4. In the bot's .env put:
 *       LINKSYNC_WEBHOOK_URL=<the /exec URL>
 *       LINKSYNC_WEBHOOK_SECRET=<the same SECRET string>
 *  5. (If you have multiple tabs) set LINKSYNC_SHEET_NAME to the tab name,
 *     otherwise the first sheet is used.
 *
 * After editing the script later, redeploy the SAME deployment
 * (Deploy ▸ Manage deployments ▸ ✏️ ▸ Version: New version) so the URL stays.
 */

const SECRET = 'CHANGE_ME_TO_A_LONG_RANDOM_STRING';

function doPost(e) {
  try {
    const body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (body.secret !== SECRET) {
      return _json({ ok: false, error: 'unauthorized' });
    }

    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = body.sheet ? ss.getSheetByName(body.sheet) : ss.getSheets()[0];
    if (!sheet) {
      return _json({ ok: false, error: 'sheet not found: ' + body.sheet });
    }

    const action = body.action || 'append';

    if (action === 'ping') {
      return _json({ ok: true, sheet: sheet.getName() });
    }

    if (action === 'list') {
      const col = Number(body.column) || 3;
      const last = sheet.getLastRow();
      if (last < 2) return _json({ ok: true, values: [] });
      const values = sheet.getRange(2, col, last - 1, 1)
        .getValues()
        .map(function (r) { return String(r[0]).trim(); })
        .filter(function (v) { return v.length > 0; });
      return _json({ ok: true, values: values });
    }

    if (action === 'append') {
      const rows = body.rows || [];
      if (rows.length > 0) {
        // Write after the last used row; never overwrite the header (row 1).
        const start = Math.max(sheet.getLastRow() + 1, 2);
        sheet.getRange(start, 1, rows.length, rows[0].length).setValues(rows);
      }
      return _json({ ok: true, appended: rows.length });
    }

    return _json({ ok: false, error: 'unknown action: ' + action });
  } catch (err) {
    return _json({ ok: false, error: String(err) });
  }
}

function doGet(e) {
  return _json({ ok: true, msg: 'IM8 Link Sync web app is live. Use POST.' });
}

function _json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
