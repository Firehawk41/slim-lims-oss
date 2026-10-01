Attribute VB_Name = "modLimsPing"
'=======================================================================
' VBA-to-Flask round trip prototype (proof of concept)
'=======================================================================
'
' PURPOSE
'   Demonstrates an Excel VBA macro calling a local Flask API (app.py)
'   and parsing its JSON response, as a proof of concept for a larger
'   VBA-to-API migration.
'
' HOW TO USE
'   1. Start the matching Flask server first (see app.py):
'         pip install flask
'         python app.py
'   2. In Excel: Alt+F11 to open the VBA editor, insert this module
'      (File > Import File... > vba_ping.bas), or paste its contents
'      into a new standard module.
'   3. In Tools > References, make sure "Microsoft WinHTTP Services"
'      is available (WinHttp.WinHttpRequest is created late-bound
'      below via CreateObject, so no reference is strictly required).
'   4. Run Ping_LimsServer directly from the VBA editor (F5), or
'      assign it to a worksheet button (Developer tab > Insert >
'      Button > Assign Macro > Ping_LimsServer).
'
' JSON HANDLING
'   This prototype hand-rolls a tiny JSON parser for the one fixed
'   shape of response we expect from /ping:
'       {"status": "ok", "server_time": "...", "log_id": 123}
'   It is NOT a general-purpose JSON parser. If this migration grows
'   beyond a handful of fixed endpoints, replace ExtractJsonString /
'   ExtractJsonNumber below with a real library reference, e.g.
'   VBA-JSON (https://github.com/VBA-tools/VBA-JSON), added via
'   Tools > References or by importing JsonConverter.bas.
'=======================================================================

Option Explicit

Sub Ping_LimsServer()

    Dim http As Object
    Dim computerName As String
    Dim requestBody As String
    Dim responseText As String
    Dim serverTime As String
    Dim logId As String

    On Error GoTo ConnectionFailed

    ' Get the local machine name from the environment.
    computerName = Environ("COMPUTERNAME")

    ' Build the JSON request body by hand (single known field, so no
    ' library needed -- just watch out for quotes/backslashes if this
    ' is ever extended to free-text fields).
    requestBody = "{""computer_name"": """ & computerName & """}"

    ' Create a WinHTTP request object (late-bound, no reference needed).
    Set http = CreateObject("WinHttp.WinHttpRequest.5.1")

    ' Timeouts, in milliseconds: resolve, connect, send, receive.
    ' Keeps the demo from hanging forever if the server isn't running.
    http.SetTimeouts 5000, 5000, 5000, 5000

    http.Open "POST", "http://localhost:5000/ping", False
    http.SetRequestHeader "Content-Type", "application/json"
    http.Send requestBody

    ' A non-200 status (e.g. our 400 for a missing field) is treated
    ' the same as a connection failure for this simple prototype.
    If http.Status <> 200 Then
        GoTo ConnectionFailed
    End If

    responseText = http.ResponseText

    ' Hand-rolled extraction of the two fields we care about.
    serverTime = ExtractJsonString(responseText, "server_time")
    logId = ExtractJsonNumber(responseText, "log_id")

    MsgBox "Server time: " & serverTime & vbCrLf & _
           "Log ID: " & logId, vbInformation, "Ping succeeded"

    Exit Sub

ConnectionFailed:
    MsgBox "Connection failed " & Chr(150) & " is the Flask server running?", _
           vbExclamation, "Ping failed"

End Sub

' Minimal hand-rolled helper: pulls out the string value for
' "key": "value" from a small, known JSON response. Not a general
' JSON parser -- assumes the key/value pair is present verbatim and
' the value contains no escaped quotes.
Private Function ExtractJsonString(ByVal json As String, ByVal key As String) As String

    Dim searchKey As String
    Dim startPos As Long
    Dim valueStart As Long
    Dim valueEnd As Long

    searchKey = """" & key & """"
    startPos = InStr(1, json, searchKey)
    If startPos = 0 Then
        ExtractJsonString = ""
        Exit Function
    End If

    ' Find the colon after the key, then the opening quote of the value.
    valueStart = InStr(startPos, json, ":")
    valueStart = InStr(valueStart, json, """") + 1
    valueEnd = InStr(valueStart, json, """")

    ExtractJsonString = Mid(json, valueStart, valueEnd - valueStart)

End Function

' Minimal hand-rolled helper: pulls out the numeric value for
' "key": 123 from a small, known JSON response.
Private Function ExtractJsonNumber(ByVal json As String, ByVal key As String) As String

    Dim searchKey As String
    Dim startPos As Long
    Dim valueStart As Long
    Dim valueEnd As Long
    Dim i As Long
    Dim ch As String

    searchKey = """" & key & """"
    startPos = InStr(1, json, searchKey)
    If startPos = 0 Then
        ExtractJsonNumber = ""
        Exit Function
    End If

    valueStart = InStr(startPos, json, ":") + 1

    ' Skip whitespace after the colon.
    Do While Mid(json, valueStart, 1) = " "
        valueStart = valueStart + 1
    Loop

    ' Read digits until we hit a non-digit (comma, brace, etc.).
    valueEnd = valueStart
    Do While valueEnd <= Len(json)
        ch = Mid(json, valueEnd, 1)
        If ch < "0" Or ch > "9" Then Exit Do
        valueEnd = valueEnd + 1
    Loop

    ExtractJsonNumber = Mid(json, valueStart, valueEnd - valueStart)

End Function
