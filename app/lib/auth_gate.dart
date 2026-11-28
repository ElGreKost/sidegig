// Decides what the person sees: sign-in, a "no access" notice, or the board.
//
// After Google sign-in it calls `GET /v1/me`. A 401 or 403 means the account
// has no access to this board. Otherwise it creates the [Board] session,
// which lives until sign out.

import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import 'api.dart';
import 'board.dart';
import 'screens/home.dart';
import 'screens/sign_in.dart';
import 'screens/status_pages.dart';

class AuthGate extends StatelessWidget {
  const AuthGate({super.key});

  @override
  Widget build(BuildContext context) => StreamBuilder<User?>(
        stream: FirebaseAuth.instance.authStateChanges(),
        builder: (context, snap) {
          if (snap.connectionState == ConnectionState.waiting && !snap.hasData) {
            return const SplashPage();
          }
          final user = snap.data;
          if (user == null) return const SignInPage();
          return SessionGate(key: ValueKey(user.uid), user: user);
        },
      );
}

/// Checks access for one signed-in user and owns their board session.
class SessionGate extends StatefulWidget {
  const SessionGate({super.key, required this.user});

  final User user;

  @override
  State<SessionGate> createState() => _SessionGateState();
}

class _SessionGateState extends State<SessionGate> {
  Board? _board;
  ApiException? _error;
  bool _noAccess = false;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _connect();
  }

  Future<void> _connect() async {
    setState(() {
      _loading = true;
      _error = null;
      _noAccess = false;
    });
    final api = ApiClient();
    try {
      final me = await api.me();
      if (!mounted) {
        api.close();
        return;
      }
      final board = Board(api: api, me: me)..start();
      setState(() {
        _board = board;
        _loading = false;
      });
    } on ApiException catch (e) {
      api.close();
      if (!mounted) return;
      setState(() {
        _loading = false;
        _noAccess = e.isAuth;
        _error = e.isAuth ? null : e;
      });
    }
  }

  Future<void> _signOut() async {
    _board?.stop();
    // Close any thread or form pages pushed on top of the board.
    Navigator.of(context).popUntil((route) => route.isFirst);
    await FirebaseAuth.instance.signOut();
  }

  @override
  void dispose() {
    _board?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const SplashPage();
    if (_noAccess) return NoAccessPage(email: widget.user.email ?? '', onSignOut: _signOut);
    final board = _board;
    if (board == null) {
      return ErrorPage(
        message: _error?.message ?? 'Could not open the board.',
        onRetry: _connect,
        onSignOut: _signOut,
      );
    }
    return HomeShell(board: board, onSignOut: _signOut);
  }
}
