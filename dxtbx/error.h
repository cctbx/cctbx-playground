/* *****************************************************
   THIS IS AN AUTOMATICALLY GENERATED FILE. DO NOT EDIT.
   *****************************************************

   Generated by:
     scitbx.generate_error_h
 */

/*! \file
    Declarations and macros for exception handling.
 */

#ifndef DXTBX_ERROR_H
#define DXTBX_ERROR_H

#include <scitbx/error_utils.h>

#define DXTBX_CHECK_POINT \
  std::cout << __FILE__ << "(" << __LINE__ << ")" << std::endl << std::flush
#define DXTBX_CHECK_POINT_MSG(msg) \
  std::cout << msg << " @ " __FILE__ << "(" << __LINE__ << ")" << std::endl << std::flush
#define DXTBX_EXAMINE(A) \
  std::cout << "variable " << #A << ": " << A << std::endl << std::flush

//! Common dxtbx namespace.
namespace dxtbx {

  //! All dxtbx exceptions are derived from this class.
  class error : public ::scitbx::error_base<error>
  {
    public:

      //! General dxtbx error message.
      explicit
      error(std::string const& msg) throw()
        : ::scitbx::error_base<error>("dxtbx", msg)
      {}

      //! Error message with file name and line number.
      /*! Used by the macros below.
       */
      error(const char* file, long line, std::string const& msg = "",
            bool internal = true) throw()
        : ::scitbx::error_base<error>("dxtbx", file, line, msg, internal)
      {}
  };

  //! Special class for "Index out of range." exceptions.
  /*! These exceptions are propagated to Python as IndexError.
   */
  class error_index : public error
  {
    public:
      //! Default constructor. The message may be customized.
      explicit
      error_index(std::string const& msg = "Index out of range.") throw()
        : error(msg)
      {}
  };

} // namespace dxtbx

//! For throwing an error exception with file name, line number, and message.
#define DXTBX_ERROR(msg) \
  SCITBX_ERROR_UTILS_REPORT(dxtbx::error, msg)
//! For throwing an "Internal Error" exception.
#define DXTBX_INTERNAL_ERROR() \
  SCITBX_ERROR_UTILS_REPORT_INTERNAL(dxtbx::error)
//! For throwing a "Not implemented" exception.
#define DXTBX_NOT_IMPLEMENTED() \
  SCITBX_ERROR_UTILS_REPORT_NOT_IMPLEMENTED(dxtbx::error)

//! Custom dxtbx assertion.
#define DXTBX_ASSERT(assertion) \
  SCITBX_ERROR_UTILS_ASSERT(dxtbx::error, DXTBX_ASSERT, assertion)

#endif // DXTBX_ERROR_H
