<?php
function error_handler($errno, $errstr, $errfile, $errline) {
    http_response_code(500);
    header("Content-Type: text/plain;charset=utf-8");
    echo "Internal error: $errstr\n";
    exit(1);
}
error_reporting(0);
set_error_handler("error_handler");

include("config.php");

if ($_SERVER["REQUEST_METHOD"] !== "POST") {
    http_response_code(400);
    header("Content-Type: text/plain;charset=utf-8");
    echo "Bad Request: this URL only accepts POST\n";
    exit(1);
}
if (!isset($_POST["blob"])) {
    http_response_code(400);
    header("Content-Type: text/plain;charset=utf-8");
    echo "Bad Request: no blob in the request data\n";
    exit(1);
}

# We know that the blob is a JSON object, so it should end with a }.
# Use this to munge the IP address of the client into the blob.
$client = $_SERVER["REMOTE_ADDR"];
$blob = preg_replace('/\}$/', ',"client_ip":"'.$client.'"}', $_POST["blob"]);

# Dump the blob to disk, encrypted.
# No, there really isn't any way to avoid going through a shell, sigh.
$bfile = tempnam($REPORT_DIR, "blob");
$proc = proc_open(
    "gpg2 --homedir " . escapeshellarg($GPG_HOME) .
        " --no-permission-warning --encrypt --sign --recipient " .
        escapeshellarg($ENCRYPT_TO),
    [
        0 => ["pipe", "r"],
        1 => ["file", $bfile, "wb"]
    ],
    $pipes);

assert($proc !== FALSE);
fwrite($pipes[0], $blob);
fclose($pipes[0]);
$rv = proc_close($proc);
assert($rv === 0);
chmod($bfile, 0640);

http_response_code(204);
?>
