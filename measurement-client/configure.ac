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

dnl Autoconf core has no good way of enabling compiler warnings.
dnl This is a cut-down version of the elaborate thing in the extras
dnl archive, which we do not need nearly all of.
dnl Partly based on:
dnl http://www.gnu.org/software/autoconf-archive/ax_append_flag.html
dnl http://www.gnu.org/software/autoconf-archive/ax_check_compile_flag.html
dnl http://www.gnu.org/software/autoconf-archive/ax_append_compile_flags.html
dnl http://www.gnu.org/software/autoconf-archive/ax_compiler_flags_cflags.html
AC_PREREQ(2.64)dnl for _AC_LANG_PREFIX and AS_VAR_IF

AC_DEFUN([AX_CHECK_COMPILE_FLAG],
[AS_VAR_PUSHDEF([CACHEVAR],[ax_cv_check_[]_AC_LANG_ABBREV[]flags_$4_$1])dnl
AC_CACHE_CHECK([whether _AC_LANG compiler accepts $1], CACHEVAR, [
  ax_check_save_flags=$[]_AC_LANG_PREFIX[]FLAGS
  _AC_LANG_PREFIX[]FLAGS="$[]_AC_LANG_PREFIX[]FLAGS $4 $1"
  AC_COMPILE_IFELSE([m4_default([$5],[AC_LANG_PROGRAM()])],
    [AS_VAR_SET(CACHEVAR,[yes])],
    [AS_VAR_SET(CACHEVAR,[no])])
  _AC_LANG_PREFIX[]FLAGS=$ax_check_save_flags])
AS_VAR_IF(CACHEVAR,yes,
  [m4_default([$2], :)],
  [m4_default([$3], :)])
AS_VAR_POPDEF([CACHEVAR])])

AC_DEFUN([AX_APPEND_FLAG],
[AS_VAR_PUSHDEF([FLAGS], [m4_default($2,_AC_LANG_PREFIX[FLAGS])])
AS_VAR_SET_IF(FLAGS,[
  AS_CASE([" AS_VAR_GET(FLAGS) "],
    [*" $1 "*], [AC_RUN_LOG([: FLAGS already contains $1])],
    [
     AS_VAR_APPEND(FLAGS,[" $1"])
     AC_RUN_LOG([: FLAGS="$FLAGS"])
    ])
  ],
  [
  AS_VAR_SET(FLAGS,[$1])
  AC_RUN_LOG([: FLAGS="$FLAGS"])
  ])
AS_VAR_POPDEF([FLAGS])dnl
])dnl AX_APPEND_FLAG

AC_DEFUN([AX_APPEND_COMPILE_FLAGS],
[for flag in $1; do
  AX_CHECK_COMPILE_FLAG([$flag],
    [AX_APPEND_FLAG([$flag], [$2])], [], [$3], [$4])
done
])

AC_DEFUN([zw_SIMPLE_ENABLE_WARNINGS],
[
    # Always pass -Werror=unknown-warning-option to get Clang to fail
    # on bad flags, otherwise they are always appended to the
    # warn_cflags variable, and Clang warns on them for every
    # compilation unit.  If this is passed to GCC, it will explode, so
    # the flag must be enabled conditionally.
    AX_CHECK_COMPILE_FLAG([-Werror=unknown-warning-option],[
        ax_compiler_flags_test="-Werror=unknown-warning-option"
    ],[
        ax_compiler_flags_test=""
    ])

    AX_APPEND_COMPILE_FLAGS([dnl
        -Wall dnl
        -Wextra dnl
        -Wpedantic dnl
        -Wstrict-prototypes dnl
        -Wwrite-strings dnl
        -Wformat=2 dnl
    ], [WARN_CFLAGS], [$ax_compiler_flags_test])

    AC_SUBST(WARN_CFLAGS)
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
zw_SIMPLE_ENABLE_WARNINGS

AC_SEARCH_LIBS([lrint], [m], [],
  [AC_MSG_FAILURE([This program needs lrint.])])

AC_SEARCH_LIBS([clock_gettime], [rt],
  [AC_DEFINE([HAVE_CLOCK_GETTIME], 1,
    [Define to 1 if you have the `clock_gettime' function.])])

AC_CHECK_FUNCS(
  [getline closefrom ppoll poll mach_absolute_time gettimeofday])

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
