/* SPDX-License-Identifier: GPL-3.0-or-later
 * VLC 3's TLS/HTTP clients use TCP Fast Open, but only fall back to connect()
 * for EOPNOTSUPP. Strict Snap rejects this sendmsg form with EACCES while
 * permitting ordinary connected TCP. Preserve confinement: report just this
 * unsupported optimisation as such, so VLC runs its own normal TCP fallback.
 * Never change the result of a connected write or any other denied operation.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stddef.h>
#include <sys/socket.h>
#include <sys/types.h>

static ssize_t (*next_sendmsg)(int, const struct msghdr *, int);

__attribute__((constructor))
static void resolve_sendmsg(void)
{
    next_sendmsg = dlsym(RTLD_NEXT, "sendmsg");
}

ssize_t sendmsg(int fd, const struct msghdr *message, int flags)
{
    if (next_sendmsg == NULL) {
        errno = ENOSYS;
        return -1;
    }
    ssize_t result = next_sendmsg(fd, message, flags);
    int error = errno;
#ifdef MSG_FASTOPEN
    if (result == -1 && error == EACCES && (flags & MSG_FASTOPEN)
            && message != NULL && message->msg_name != NULL
            && message->msg_namelen >= sizeof(sa_family_t)) {
        const struct sockaddr *peer = message->msg_name;
        int type = 0;
        socklen_t length = sizeof(type);
        if ((peer->sa_family == AF_INET || peer->sa_family == AF_INET6)
                && getsockopt(fd, SOL_SOCKET, SO_TYPE, &type, &length) == 0
                && type == SOCK_STREAM)
            error = EOPNOTSUPP;
    }
#endif
    errno = error;
    return result;
}
