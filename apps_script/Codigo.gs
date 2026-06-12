/**
 * TICANET - Apps Script
 * Ver repositorio para documentación completa.
 * Marcadores: {{CALLSIGN}}, {{EVENT}}, {{DATE}}, {{TIME_UTC}}, {{CODE}}, {{NUMBER}}
 */

const TEMPLATE_ID_DEFAULT = "YOUR_DEFAULT_TEMPLATE_ID";
const CODES_SHEET_NAME = "Codes";
const SENDER_NAME = "TICANET Bot - QSL Digital";
const EMAIL_SUBJECT = "Tu QSL Digital TICANET - {{EVENT}}";

const COL = {
  CALLSIGN: 0, CODE: 1, EVENT_ID: 2, EVENT_NAME: 3,
  TIMESTAMP: 4, USED: 5, TEMPLATE_ID: 6, NUMBER: 7,
  USED_DATE: 8, EMAIL: 9
};

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName(CODES_SHEET_NAME);
    if (!sheet) {
      sheet = ss.insertSheet(CODES_SHEET_NAME);
      sheet.appendRow(["callsign","code","event_id","event_name","timestamp","used","template_id","number"]);
    }
    sheet.appendRow([
      data.callsign.toUpperCase(),
      "'" + data.code,
      data.event_id || "",
      data.event_name || "",
      data.timestamp || new Date().toISOString(),
      "NO",
      data.template_id || "",
      data.number || ""
    ]);
    return ContentService.createTextOutput(JSON.stringify({status:"ok"})).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({status:"error",message:err.toString()})).setMimeType(ContentService.MimeType.JSON);
  }
}

function onFormSubmit(e) {
  var responses = e.namedValues;
  var callsign = "", code = "", email = "";
  for (var key in responses) {
    var val = responses[key][0] || "";
    if (key.indexOf("Indicativo") > -1) callsign = val.toUpperCase().trim();
    else if (key.indexOf("TICANET") > -1 && key.indexOf("digo") > -1) code = val.trim();
    else if (key === "email") email = val.trim();
  }
  if (!callsign || !code || !email) { Logger.log("Datos incompletos: " + JSON.stringify(responses)); return; }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var codesSheet = ss.getSheetByName(CODES_SHEET_NAME);
  if (!codesSheet) { sendErrorEmail(email, callsign, "Error interno."); return; }

  var data = codesSheet.getDataRange().getValues();
  var found = false, eventName = "", eventDate = "", timeUtc = "", participantNumber = "", templateId = TEMPLATE_ID_DEFAULT, rowIndex = -1;

  for (var i = 1; i < data.length; i++) {
    if (data[i][COL.CALLSIGN].toString().toUpperCase().split("-")[0] === callsign &&
        data[i][COL.CODE].toString() === code) {
      found = true;
      eventName = data[i][COL.EVENT_NAME] || "TICANET Activity";
      rowIndex = i + 1;
      var dateTime = parseTimestamp(data[i][COL.TIMESTAMP]);
      eventDate = dateTime.date; timeUtc = dateTime.time;
      var rowTemplateId = (data[i][COL.TEMPLATE_ID] || "").toString().trim();
      if (rowTemplateId) templateId = rowTemplateId;
      participantNumber = (data[i][COL.NUMBER] || "").toString().trim();
      if (!participantNumber) participantNumber = "?";
      break;
    }
  }

  if (!found) { sendErrorEmail(email, callsign, "El codigo " + code + " no coincide con " + callsign + "."); return; }
  if (data[rowIndex - 1][COL.USED] === "SI") { sendErrorEmail(email, callsign, "Este codigo ya fue utilizado."); return; }

  try {
    var pdfBlob = generateQSL(callsign, eventName, eventDate, timeUtc, code, participantNumber, templateId);
    var subject = EMAIL_SUBJECT.replace("{{EVENT}}", eventName);
    GmailApp.sendEmail(email, subject, "", { name: SENDER_NAME, htmlBody: getEmailHTML(callsign, eventName, eventDate, timeUtc), attachments: [pdfBlob] });
    codesSheet.getRange(rowIndex, COL.USED + 1).setValue("SI");
    codesSheet.getRange(rowIndex, COL.USED_DATE + 1).setValue(new Date().toISOString());
    codesSheet.getRange(rowIndex, COL.EMAIL + 1).setValue(email);
    Logger.log("QSL enviada a " + email + " para " + callsign);
  } catch (err) {
    Logger.log("Error generando/enviando QSL: " + err.toString());
    sendErrorEmail(email, callsign, "Hubo un error generando tu QSL.");
  }
}

function generateQSL(callsign, eventName, eventDate, timeUtc, code, number, templateId) {
  var template = DriveApp.getFileById(templateId);
  var copy = template.makeCopy("QSL_" + callsign + "_" + code);
  var slideId = copy.getId();
  var presentation = SlidesApp.openById(slideId);
  presentation.getSlides().forEach(function(slide) {
    slide.replaceAllText("{{CALLSIGN}}", callsign);
    slide.replaceAllText("{{EVENT}}", eventName);
    slide.replaceAllText("{{DATE}}", eventDate);
    slide.replaceAllText("{{TIME_UTC}}", timeUtc);
    slide.replaceAllText("{{CODE}}", code);
    slide.replaceAllText("{{NUMBER}}", number);
  });
  presentation.saveAndClose();
  var pdfBlob = DriveApp.getFileById(slideId).getAs("application/pdf").setName("QSL_TICANET_" + callsign + "_" + eventDate.replace(/\//g, "-") + ".pdf");
  DriveApp.getFileById(slideId).setTrashed(true);
  return pdfBlob;
}

function parseTimestamp(tsRaw) {
  var hours, minutes, day, month, year;
  if (typeof tsRaw === "string") {
    var parts = tsRaw.split(/[- :T]/);
    year = parts[0]; month = parts[1]; day = parts[2];
    hours = parts[3] || "00"; minutes = parts[4] || "00";
  } else if (tsRaw instanceof Date) {
    day = ("0" + tsRaw.getDate()).slice(-2);
    month = ("0" + (tsRaw.getMonth() + 1)).slice(-2);
    year = tsRaw.getFullYear();
    hours = ("0" + tsRaw.getHours()).slice(-2);
    minutes = ("0" + tsRaw.getMinutes()).slice(-2);
  } else { var d = new Date(); day = ("0"+d.getUTCDate()).slice(-2); month = ("0"+(d.getUTCMonth()+1)).slice(-2); year = d.getUTCFullYear(); hours = ("0"+d.getUTCHours()).slice(-2); minutes = ("0"+d.getUTCMinutes()).slice(-2); }
  return { date: day + "/" + month + "/" + year, time: ("0"+hours).slice(-2) + ":" + ("0"+minutes).slice(-2) + "z" };
}

function getEmailHTML(callsign, eventName, eventDate, timeUtc) {
  return '<div style="font-family:Arial,sans-serif;max-width:600px"><h2 style="color:#2e7d32">Tu QSL Digital TICANET</h2><p>Hola <strong>'+callsign+'</strong>,</p><p>Gracias por participar en <strong>'+eventName+'</strong> el dia '+eventDate+' a las '+timeUtc+' UTC.</p><p>Adjunto tu QSL digital en PDF.</p><p>73 de <strong>TICANET</strong><br>Operado por TI3WTI - RadioLab TEC / TI0ARC</p><hr style="border:1px solid #ccc"><p style="font-size:12px;color:#666">Mensaje automatico. No responder.</p></div>';
}

function sendErrorEmail(email, callsign, message) {
  GmailApp.sendEmail(email, "TICANET - Problema con tu solicitud de QSL", "", { name: SENDER_NAME, htmlBody: '<div style="font-family:Arial,sans-serif;max-width:600px"><h2 style="color:#c62828">TICANET - Solicitud de QSL</h2><p>Hola <strong>'+callsign+'</strong>,</p><p>'+message+'</p><p>73 de <strong>TICANET</strong></p></div>' });
}
