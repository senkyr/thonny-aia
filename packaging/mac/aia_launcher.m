// Native launcher: preserve file arguments and resolve the embedded runtime after moves.
#import <Foundation/Foundation.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
    @autoreleasepool {
        NSString *bundle = [[NSBundle mainBundle] bundlePath];
        NSString *python = [bundle stringByAppendingPathComponent:
            @"Contents/MacOS/Python"];
        NSString *script = [bundle stringByAppendingPathComponent:
            @"Contents/Resources/launch.py"];
        setenv("THONNY_APP_PATH", bundle.fileSystemRepresentation, 1);
        char **args = calloc((size_t)argc + 5, sizeof(char *));
        if (!args) return 1;
        args[0] = (char *)python.fileSystemRepresentation;
        args[1] = "-I";
        args[2] = "-B";
        args[3] = (char *)script.fileSystemRepresentation;
        for (int i = 1; i < argc; i++) args[i + 3] = argv[i];
        execv(args[0], args);
        perror("Cannot launch Thonny AIA's bundled Python");
        free(args);
        return 1;
    }
}
