dnl As of this writing (April 2016), Autoconf 2.70 is not yet released.
dnl Backport some improvements:
dnl  - switch AC_CHECK_HEADER to compile-only
dnl  - eliminate unnecessary tests in AC_INCLUDES_DEFAULT
dnl  - Darwin (OSX) support in AC_USE_SYSTEM_EXTENSIONS
AC_PREREQ([2.62])dnl earliest version with working m4_version_prereq
m4_version_prereq([2.70], [], [
  m4_define([AC_CHECK_HEADER], [_AC_CHECK_HEADER_COMPILE($@)])
  AC_DEFUN_ONCE([_AC_INCLUDES_DEFAULT_REQUIREMENTS],
    [m4_divert_text([DEFAULTS],[ac_includes_default=""
])])
  m4_define([AC_USE_SYSTEM_EXTENSIONS],
    m4_defn([AC_USE_SYSTEM_EXTENSIONS])[
    AH_VERBATIM([USE_SYSTEM_EXTENSIONS_270],
[/* Enable general extensions on OSX. */
#ifndef _DARWIN_C_SOURCE
# undef _DARWIN_C_SOURCE
#endif])
    AC_DEFINE([_DARWIN_C_SOURCE])
  ])
])

AC_INIT([probe-core], [1.0],
  [https://github.com/zackw/active-geolocator/issues/new])

AC_CONFIG_SRCDIR([probe-core.c])
AC_CONFIG_HEADERS([config.h])
AC_CONFIG_FILES([Makefile])

AC_PROG_CC_C99
AS_IF([test "x$ac_cv_prog_cc_c99" = xno],
  [AC_MSG_FAILURE([This program needs a C99-compliant compiler.])])

AC_USE_SYSTEM_EXTENSIONS
AC_SYS_LARGEFILE

AC_CHECK_FUNCS([getline closefrom ppoll poll mach_absolute_time gettimeofday])
AC_SEARCH_LIBS([clock_gettime], [-lrt])
AC_SEARCH_LIBS([floor], [-lm])

AS_IF([test "x$ac_cv_func_mach_absolute_time" = xno &&
       test "x$ac_cv_search_clock_gettime" = xno &&
       test "x$ac_cv_func_gettimeofday" = xno],
  [AC_MSG_FAILURE(
    [This program needs clock_gettime, mach_absolute_time, or
    at least gettimeofday.])])

AS_IF([test "x$ac_cv_func_ppoll" = xno &&
       test "x$ac_cv_func_poll" = xno],
  [AC_MSG_FAILURE(
    [This program needs either ppoll or poll.])])

AC_OUTPUT
