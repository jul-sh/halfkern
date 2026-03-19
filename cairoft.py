#!/usr/bin/python3

import ctypes as ct
import glob
import os
import platform

import cairo


class PycairoContext(ct.Structure):
    _fields_ = [
        ("PyObject_HEAD", ct.c_byte * object.__basicsize__),
        ("ctx", ct.c_void_p),
        ("base", ct.c_void_p),
    ]


def _resolve_nix_lib(name):
    """Find the full path to a shared library via NIX_LDFLAGS."""
    ext = ".dylib" if platform.system() == "Darwin" else ".so"
    nix_ldflags = os.environ.get("NIX_LDFLAGS", "")
    for token in nix_ldflags.split():
        if token.startswith("-L"):
            d = token[2:]
            for candidate in glob.glob(os.path.join(d, f"lib{name}*{ext}")):
                if os.path.isfile(candidate):
                    return candidate
    return None


def _load_lib(name):
    """Load a shared library, resolving via NIX_LDFLAGS if needed."""
    try:
        return ct.CDLL(name)
    except OSError:
        # Try resolving via Nix (macOS SIP strips DYLD_* vars)
        base = name
        for prefix in ("lib",):
            if base.startswith(prefix):
                base = base[len(prefix):]
        for suffix in (".dylib", ".so", ".so.6", ".so.2"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        resolved = _resolve_nix_lib(base)
        if resolved:
            return ct.CDLL(resolved)
        raise


_initialized = False


def create_cairo_font_face_for_file(filename, faceindex=0, loadoptions=0):
    "given the name of a font file, and optional faceindex to pass to FT_New_Face" \
    " and loadoptions to pass to cairo_ft_font_face_create_for_ft_face, creates" \
    " a cairo.FontFace object that may be used to render text with that font."
    global _initialized
    global _freetype_so
    global _cairo_so
    global _ft_lib
    global _ft_destroy_key
    global _surface

    CAIRO_STATUS_SUCCESS = 0
    FT_Err_Ok = 0

    if not _initialized:
        # find shared objects
        if platform.system() == "Darwin":
            _freetype_so = _load_lib("libfreetype.dylib")
            _cairo_so = _load_lib("libcairo.dylib")
        else:
            _freetype_so = _load_lib("libfreetype.so.6")
            _cairo_so = _load_lib("libcairo.so.2")
        _cairo_so.cairo_ft_font_face_create_for_ft_face.restype = ct.c_void_p
        _cairo_so.cairo_ft_font_face_create_for_ft_face.argtypes = [
            ct.c_void_p,
            ct.c_int,
        ]
        _cairo_so.cairo_font_face_get_user_data.restype = ct.c_void_p
        _cairo_so.cairo_font_face_get_user_data.argtypes = (ct.c_void_p, ct.c_void_p)
        _cairo_so.cairo_font_face_set_user_data.argtypes = (
            ct.c_void_p,
            ct.c_void_p,
            ct.c_void_p,
            ct.c_void_p,
        )
        _cairo_so.cairo_set_font_face.argtypes = [ct.c_void_p, ct.c_void_p]
        _cairo_so.cairo_font_face_status.argtypes = [ct.c_void_p]
        _cairo_so.cairo_font_face_destroy.argtypes = (ct.c_void_p,)
        _cairo_so.cairo_status.argtypes = [ct.c_void_p]
        # initialize freetype
        _ft_lib = ct.c_void_p()
        status = _freetype_so.FT_Init_FreeType(ct.byref(_ft_lib))
        if status != FT_Err_Ok:
            raise RuntimeError("Error %d initializing FreeType library." % status)

        _surface = cairo.ImageSurface(cairo.FORMAT_A8, 0, 0)
        _ft_destroy_key = ct.c_int()  # dummy address
        _initialized = True

    ft_face = ct.c_void_p()
    cr_face = None
    try:
        # load FreeType face
        status = _freetype_so.FT_New_Face(
            _ft_lib, filename.encode("utf-8"), faceindex, ct.byref(ft_face)
        )
        if status != FT_Err_Ok:
            raise RuntimeError(
                "Error %d creating FreeType font face for %s" % (status, filename)
            )

        # create Cairo font face for freetype face
        cr_face = _cairo_so.cairo_ft_font_face_create_for_ft_face(ft_face, loadoptions)
        status = _cairo_so.cairo_font_face_status(cr_face)
        if status != CAIRO_STATUS_SUCCESS:
            raise RuntimeError(
                "Error %d creating cairo font face for %s" % (status, filename)
            )

        if (
            _cairo_so.cairo_font_face_get_user_data(cr_face, ct.byref(_ft_destroy_key))
            == None
        ):
            status = _cairo_so.cairo_font_face_set_user_data(
                cr_face, ct.byref(_ft_destroy_key), ft_face, _freetype_so.FT_Done_Face
            )
            if status != CAIRO_STATUS_SUCCESS:
                raise RuntimeError(
                    "Error %d doing user_data dance for %s" % (status, filename)
                )
            ft_face = None  # Cairo has stolen my reference

        # set Cairo font face into Cairo context
        cairo_ctx = cairo.Context(_surface)
        cairo_t = PycairoContext.from_address(id(cairo_ctx)).ctx
        _cairo_so.cairo_set_font_face(cairo_t, cr_face)
        status = _cairo_so.cairo_font_face_status(cairo_t)
        if status != CAIRO_STATUS_SUCCESS:
            raise RuntimeError(
                "Error %d creating cairo font face for %s" % (status, filename)
            )

    finally:
        _cairo_so.cairo_font_face_destroy(cr_face)
        _freetype_so.FT_Done_Face(ft_face)

    # get back Cairo font face as a Python object
    face = cairo_ctx.get_font_face()
    return face


if __name__ == "__main__":
    import sys

    face = create_cairo_font_face_for_file(sys.argv[1], 0)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 128, 128)
    ctx = cairo.Context(surface)

    ctx.set_font_face(face)
    ctx.set_font_size(30)
    ctx.move_to(0, 44)
    ctx.show_text("Hello,")
    ctx.move_to(30, 74)
    ctx.show_text("world!")

    del ctx

    surface.write_to_png("hello.png")
