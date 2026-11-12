# PAM interface in python, launches compare.py
# Gehaertete Version: Timeout gegen Zombie-Kamera, sauberer Fallback, robuste Konversation

import subprocess
import os
import glob
import syslog
import configparser

config = configparser.ConfigParser()
config.read(os.path.dirname(os.path.abspath(__file__)) + "/config.ini")

# Hartes Zeitlimit fuer compare.py: haengende Kamera darf sudo nie blockieren
COMPARE_TIMEOUT = int(config.get("core", "compare_timeout", fallback=15))


def _conv(pamh, msgtype, text):
    """Konversation darf den Auth-Stack unter keinen Umstaenden abwuergen."""
    try:
        pamh.conversation(pamh.Message(msgtype, text))
    except Exception:
        pass


def doAuth(pamh):
    if config.getboolean("core", "disabled"):
            return pamh.PAM_AUTHINFO_UNAVAIL

    if config.getboolean("core", "ignore_ssh"):
            if "SSH_CONNECTION" in os.environ or "SSH_CLIENT" in os.environ or "SSHD_OPTS" in os.environ:
                    return pamh.PAM_AUTHINFO_UNAVAIL

    if config.getboolean("core", "ignore_closed_lid"):
            if any("closed" in open(f).read() for f in glob.glob("/proc/acpi/button/lid/*/state")):
                    return pamh.PAM_AUTHINFO_UNAVAIL

    syslog.openlog("[HOWDY]", 0, syslog.LOG_AUTH)

    if config.getboolean("core", "detection_notice"):
            _conv(pamh, pamh.PAM_TEXT_INFO, "Attempting face detection")

    syslog.syslog(syslog.LOG_INFO, "Attempting facial authentication for user " + pamh.get_user())

    proc = subprocess.Popen(["/usr/bin/python3",
                             os.path.dirname(os.path.abspath(__file__)) + "/compare.py",
                             pamh.get_user()])
    try:
            status = proc.wait(timeout=COMPARE_TIMEOUT)
    except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            syslog.syslog(syslog.LOG_INFO, "Failure, compare timed out (camera stuck?)")
            syslog.closelog()
            return pamh.PAM_AUTH_ERR

    if status == 10:
            if not config.getboolean("core", "suppress_unknown"):
                    _conv(pamh, pamh.PAM_ERROR_MSG, "No face model known")
            syslog.syslog(syslog.LOG_NOTICE, "Failure, no face model known")
            syslog.closelog()
            return pamh.PAM_USER_UNKNOWN
    elif status == 11:
            _conv(pamh, pamh.PAM_ERROR_MSG, "Face detection timeout reached")
            syslog.syslog(syslog.LOG_INFO, "Failure, timeout reached")
            syslog.closelog()
            return pamh.PAM_AUTH_ERR
    elif status == 12:
            syslog.syslog(syslog.LOG_INFO, "Failure, general abort")
            syslog.closelog()
            return pamh.PAM_AUTH_ERR
    elif status == 13:
            syslog.syslog(syslog.LOG_INFO, "Failure, image too dark")
            syslog.closelog()
            _conv(pamh, pamh.PAM_ERROR_MSG, "Face detection image too dark")
            return pamh.PAM_AUTH_ERR
    elif status == 0:
            if not config.getboolean("core", "no_confirmation"):
                    _conv(pamh, pamh.PAM_TEXT_INFO, "Identified face as " + pamh.get_user())
            syslog.syslog(syslog.LOG_INFO, "Login approved")
            syslog.closelog()
            return pamh.PAM_SUCCESS

    # Unbekannter Fehler (z. B. Kamera tot): sauber an pam_unix durchreichen
    _conv(pamh, pamh.PAM_ERROR_MSG, "Howdy error: " + str(status))
    syslog.syslog(syslog.LOG_INFO, "Failure, unknown error " + str(status))
    syslog.closelog()
    return pamh.PAM_AUTH_ERR


def pam_sm_authenticate(pamh, flags, args):
    return doAuth(pamh)


def pam_sm_open_session(pamh, flags, args):
    return doAuth(pamh)


def pam_sm_close_session(pamh, flags, argv):
    return pamh.PAM_SUCCESS


def pam_sm_setcred(pamh, flags, argv):
    return pamh.PAM_SUCCESS
