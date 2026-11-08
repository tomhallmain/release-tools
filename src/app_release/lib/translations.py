import gettext
import locale
import logging
import os
import sys

# A child of app_release.log's logger, so it reaches the same handlers;
# importing that module here would be circular, since it translates its messages.
logger = logging.getLogger("app_release.translations")


def _default_user_language():
    """The user's language as ``ll_CC`` (or ``ll``), or None when unknown."""
    if sys.platform == "win32":
        try:
            import ctypes
            return locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        except (AttributeError, OSError):
            return None
    return locale.getlocale()[0]


def _language(value) -> str:
    """The language part of a locale name ("de" from "de_DE.UTF-8"); "en" for none, C or POSIX."""
    value = (value or "").split(".")[0].split("_")[0]
    return "en" if value in ("", "C", "POSIX") else value


_locale = _language(os.environ.get("LANG") or _default_user_language())


class I18N:
    localedir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'locale')
    locale = _locale
    # Without a catalog for the language (or any catalog at all), strings stay as written.
    translate = gettext.translation('base', localedir, languages=[_locale], fallback=True)

    @staticmethod
    def install_locale(locale, verbose=True):
        I18N.locale = locale
        I18N.translate = gettext.translation('base', I18N.localedir, languages=[locale], fallback=True)
        I18N.translate.install()
        if verbose:
            logger.info(I18N._("Switched locale to: {0}").format(locale))

    @staticmethod
    def _(s):
        # return gettext.gettext(s)
        try:
            return I18N.translate.gettext(s)
        except KeyError:
            return s


'''
NOTE when gathering the translation strings, set _() == to gettext.gettext() instead of the above, and run:

    ```python C:\\Python310\\Tools\\i18n\\pygettext.py -d base -o locale\\base.pot .```

in src\\app_release. The POT output file can be used as source for the PO files in each locale.
Run personal script C:\\Scripts\\i18n_manager.py to generate new PO files and look for invalid translations.

Bonus command:
    ```git diff src\\app_release\\locale\\de\\LC_MESSAGES\\base.po src\\app_release\\locale\\de\\LC_MESSAGES\\base1.po | rg -v "^.*#" | rg -C 3 "^(-|\\+)"```

Then for each locale once the PO files are set up as desired, run below in the deepest locale directory to produce the MO file from the PO file:
    ```python C:\\Python310\\Tools\\i18n\\msgfmt.py -o base.mo base```
'''

_ = I18N._
