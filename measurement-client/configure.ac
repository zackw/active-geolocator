dnl As of this writing (April 2016), Autoconf 2.70 is not yet released.
dnl Backport some improvements:
dnl  - switch AC_CHECK_HEADER to compile-only
dnl  - eliminate unnecessary tests in AC_INCLUDES_DEFAULT
dnl  - Darwin (OSX) support in AC_USE_SYSTEM_EXTENSIONS
dnl  - C11 mode by default in AC_PROG_CC, falling back to C99
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
#endif
])
    AC_DEFINE([_DARWIN_C_SOURCE])
  ])

dnl Prior to 2.70, AC_PROG_CC ends with an unconditional call to
dnl _AC_PROG_CC_C89.  Use this as an extension hook, replacing it with
dnl the logic that will be used in 2.70.
m4_define([_AC_PROG_CC_C89_original], m4_defn([_AC_PROG_CC_C89]))
m4_define([_AC_PROG_CC_C89], [dnl
dnl Set ac_prog_cc_stdc to the supported C version.
dnl Also set the documented variable ac_cv_prog_cc_stdc;
dnl its name was chosen when it was cached, but it is no longer cached.
_AC_PROG_CC_C11([ac_prog_cc_stdc=c11
                 ac_cv_prog_cc_stdc=$ac_cv_prog_cc_c11],
  [_AC_PROG_CC_C99([ac_prog_cc_stdc=c99
                    ac_cv_prog_cc_stdc=$ac_cv_prog_cc_c99],
     [_AC_PROG_CC_C89_original([ac_prog_cc_stdc=c89
                       ac_cv_prog_cc_stdc=$ac_cv_prog_cc_c89],
                      [ac_prog_cc_stdc=no
                       ac_cv_prog_cc_stdc=no])])])
])
dnl Must also supply the definition of _AC_PROG_CC_C11.
dnl This is also taken verbatim from Autoconf trunk.
dnl I regret the bulk.
AC_DEFUN([_AC_PROG_CC_C11],
[_AC_C_STD_TRY([c11],
[[#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>
#include <wchar.h>
#include <stdio.h>

// Check varargs macros.  These examples are taken from C99 6.10.3.5.
#define debug(...) fprintf (stderr, __VA_ARGS__)
#define showlist(...) puts (#__VA_ARGS__)
#define report(test,...) ((test) ? puts (#test) : printf (__VA_ARGS__))
static void
test_varargs_macros (void)
{
  int x = 1234;
  int y = 5678;
  debug ("Flag");
  debug ("X = %d\n", x);
  showlist (The first, second, and third items.);
  report (x>y, "x is %d but y is %d", x, y);
}

// Check long long types.
#define BIG64 18446744073709551615ull
#define BIG32 4294967295ul
#define BIG_OK (BIG64 / BIG32 == 4294967297ull && BIG64 % BIG32 == 0)
#if !BIG_OK
  your preprocessor is broken;
#endif
#if BIG_OK
#else
  your preprocessor is broken;
#endif
static long long int bignum = -9223372036854775807LL;
static unsigned long long int ubignum = BIG64;

struct incomplete_array
{
  int datasize;
  double data[];
};

struct named_init {
  int number;
  const wchar_t *name;
  double average;
};

typedef const char *ccp;

static inline int
test_restrict (ccp restrict text)
{
  // See if C++-style comments work.
  // Iterate through items via the restricted pointer.
  // Also check for declarations in for loops.
  for (unsigned int i = 0; *(text+i) != '\0'; ++i)
    continue;
  return 0;
}

// Check varargs and va_copy.
static bool
test_varargs (const char *format, ...)
{
  va_list args;
  va_start (args, format);
  va_list args_copy;
  va_copy (args_copy, args);

  const char *str = "";
  int number = 0;
  float fnumber = 0;

  while (*format)
    {
      switch (*format++)
        {
        case 's': // string
          str = va_arg (args_copy, const char *);
          break;
        case 'd': // int
          number = va_arg (args_copy, int);
          break;
        case 'f': // float
          fnumber = va_arg (args_copy, double);
          break;
        default:
          break;
        }
    }
  va_end (args_copy);
  va_end (args);

  return *str && number && fnumber;
}

// Check _Alignas.
char _Alignas (double) aligned_as_double;
char _Alignas (0) no_special_alignment;
extern char aligned_as_int;
char _Alignas (0) _Alignas (int) aligned_as_int;

// Check _Alignof.
enum
{
  int_alignment = _Alignof (int),
  int_array_alignment = _Alignof (int[100]),
  char_alignment = _Alignof (char)
};
_Static_assert (0 < -_Alignof (int), "_Alignof is signed");

// Check _Noreturn.
int _Noreturn does_not_return (void) { for (;;) continue; }

// Check _Static_assert.
struct test_static_assert
{
  int x;
  _Static_assert (sizeof (int) <= sizeof (long int),
                  "_Static_assert does not work in struct");
  long int y;
};

// Check UTF-8 literals.
#define u8 syntax error!
char const utf8_literal[] = u8"happens to be ASCII" "another string";

// Check duplicate typedefs.
typedef long *long_ptr;
typedef long int *long_ptr;
typedef long_ptr long_ptr;

// Anonymous structures and unions -- taken from C11 6.7.2.1 Example 1.
struct anonymous
{
  union {
    struct { int i; int j; };
    struct { int k; long int l; } w;
  };
  int m;
} v1;
]],
[[
  // Check bool.
  _Bool success = false;

  // Check restrict.
  if (test_restrict ("String literal") == 0)
    success = true;
  char *restrict newvar = "Another string";

  // Check varargs.
  success &= test_varargs ("s, d' f .", "string", 65, 34.234);
  test_varargs_macros ();

  // Check flexible array members.
  struct incomplete_array *ia =
    malloc (sizeof (struct incomplete_array) + (sizeof (double) * 10));
  ia->datasize = 10;
  for (int i = 0; i < ia->datasize; ++i)
    ia->data[i] = i * 1.234;

  // Check named initializers.
  struct named_init ni = {
    .number = 34,
    .name = L"Test wide string",
    .average = 543.34343,
  };

  ni.number = 58;

  int dynamic_array[ni.number];
  dynamic_array[ni.number - 1] = 543;

  // work around unused variable warnings
  return (!success || bignum == 0LL || ubignum == 0uLL || newvar[0] == 'x'
          || dynamic_array[ni.number - 1] != 543);

  v1.i = 2;
  v1.w.k = 5;
  _Static_assert ((offsetof (struct anonymous, i)
                   == offsetof (struct anonymous, w.k)),
                  "Anonymous union alignment botch");
]],
dnl Try
dnl GCC         -std=gnu11 (unused restrictive mode: -std=c11)
dnl with extended modes being tried first.
dnl
dnl Do not try -qlanglvl=extc1x, because IBM XL C V12.1 (the latest version as
dnl of September 2012) does not pass the C11 test.  For now, try extc1x when
dnl compiling the C99 test instead, since it enables _Static_assert and
dnl _Noreturn, which is a win.  If -qlanglvl=extc11 or -qlanglvl=extc1x passes
dnl the C11 test in some future version of IBM XL C, we'll add it here,
dnl preferably extc11.
[[-std=gnu11]], [$1], [$2])[]dnl
])# _AC_PROG_CC_C11


])dnl m4_version_prereq([2.70], ...)


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

    # Don't enable -pedantic if we don't have C11, or we may get junk
    # warnings about static_assert.
    ax_candidate_warnings="dnl
        -Wall dnl
        -Wextra dnl
        -Wstrict-prototypes dnl
        -Wwrite-strings dnl
        -Wformat=2 dnl
    "
    if test $ac_prog_cc_stdc = c11; then
        ax_candidate_warnings="$ax_candidate_warnings -Wpedantic"
    fi

    AX_APPEND_COMPILE_FLAGS(
        [$ax_candidate_warnings], [WARN_CFLAGS],
        [$ax_compiler_flags_test])

    AC_SUBST(WARN_CFLAGS)
])

AC_INIT([probe-core], [1.0],
  [https://github.com/zackw/active-geolocator/issues/new])

AC_CONFIG_SRCDIR([probe-core-common.c])
AC_CONFIG_HEADERS([config.h])
AC_CONFIG_FILES([Makefile])

AC_PROG_CC
AS_IF([test "x$ac_cv_prog_cc_c99" = xno],
  [AC_MSG_FAILURE([This program needs a C99-compliant compiler.])])

AC_USE_SYSTEM_EXTENSIONS
AC_SYS_LARGEFILE
zw_SIMPLE_ENABLE_WARNINGS

dnl OSX ships an <assert.h> that does not expose C11's
dnl static_assert(), even in -std=c11 mode.  Autoconf doesn't have
dnl AC_PROG_CC_C11 yet.
AC_CACHE_CHECK([for _Static_assert], [ac_cv_c__Static_assert],
[AC_COMPILE_IFELSE([AC_LANG_PROGRAM([],
[[
    _Static_assert(sizeof(int) >= 1, "this assertion should succeed");
]])],
# if successful
  [AC_COMPILE_IFELSE([AC_LANG_PROGRAM([],
  [[
      _Static_assert(sizeof(int) < 1, "this assertion should fail");
  ]])],
  # if the second compilation succeeded, _Static_assert doesn't work
    [ac_cv_c__Static_assert=no],
  # if the second compilation _failed_, _Static_assert does work
    [ac_cv_c__Static_assert=yes])],
# if the first compilation failed, _Static_assert is not available or
# doesn't work
  [ac_cv_c__Static_assert=no])
])
if test $ac_cv_c__Static_assert = yes; then
  AC_DEFINE([HAVE__STATIC_ASSERT], 1,
    [Define to 1 if your C compiler supports _Static_assert, in C99 mode.])
fi


dnl N.B. we assume that, if you have a particular function, then you
dnl have the header file it's supposed to be declared in.

AC_SEARCH_LIBS([lrint], [m], [],
  [AC_MSG_FAILURE([This program needs lrint.])])

AC_SEARCH_LIBS([clock_gettime], [rt],
  [AC_DEFINE([HAVE_CLOCK_GETTIME], 1,
    [Define to 1 if you have the `clock_gettime' function.])])

AC_CHECK_FUNCS(
  [mach_absolute_time gettimeofday ppoll poll mmap closefrom])

AS_IF([test "x$ac_cv_func_mmap" = xno],
  [AC_MSG_FAILURE(
    [This program needs mmap.])])

AS_IF([test "x$ac_cv_func_ppoll" = xno &&
       test "x$ac_cv_func_poll" = xno],
  [AC_MSG_FAILURE(
    [This program needs either ppoll or poll.])])

AS_IF([test "x$ac_cv_func_mach_absolute_time" = xno &&
       test "x$ac_cv_search_clock_gettime" = xno &&
       test "x$ac_cv_func_gettimeofday" = xno],
  [AC_MSG_FAILURE(
    [This program needs clock_gettime, mach_absolute_time, or
    at least gettimeofday.])])

AC_OUTPUT
