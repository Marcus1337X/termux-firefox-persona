#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <X11/Xlib.h>
#include <X11/Xatom.h>

int main(int argc, char **argv) {
    char *disp_name = argc > 1 ? argv[1] : NULL;
    int height = argc > 2 ? atoi(argv[2]) : 40;
    Display *d = XOpenDisplay(disp_name);
    if (!d) return 1;
    int s = DefaultScreen(d);
    int sw = DisplayWidth(d, s);
    int sh = DisplayHeight(d, s);

    Window w = XCreateSimpleWindow(d, RootWindow(d, s), 0, sh - height, sw, height, 0, 0, 0);
    
    // Set window type to DOCK
    Atom type_atom = XInternAtom(d, "_NET_WM_WINDOW_TYPE", False);
    Atom dock_atom = XInternAtom(d, "_NET_WM_WINDOW_TYPE_DOCK", False);
    XChangeProperty(d, w, type_atom, XA_ATOM, 32, PropModeReplace, (unsigned char *)&dock_atom, 1);

    // Set strut partial: bottom strut
    long strut[12] = {0, 0, 0, height, 0, 0, 0, 0, 0, 0, 0, sw};
    Atom strut_atom = XInternAtom(d, "_NET_WM_STRUT_PARTIAL", False);
    XChangeProperty(d, w, strut_atom, XA_CARDINAL, 32, PropModeReplace, (unsigned char *)strut, 12);
    
    Atom strut_old = XInternAtom(d, "_NET_WM_STRUT", False);
    XChangeProperty(d, w, strut_old, XA_CARDINAL, 32, PropModeReplace, (unsigned char *)strut, 4);

    XMapWindow(d, w);
    XFlush(d);

    while (1) {
        pause();
    }
    return 0;
}
