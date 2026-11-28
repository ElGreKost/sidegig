// Landing page: sign-in only. Kept as its own widget so a fuller landing page
// can replace it later without touching the auth flow.

import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';

import '../widgets.dart';

class SignInPage extends StatefulWidget {
  const SignInPage({super.key});

  @override
  State<SignInPage> createState() => _SignInPageState();
}

class _SignInPageState extends State<SignInPage> {
  bool _busy = false;
  String? _error;

  static const _quietCodes = {'popup-closed-by-user', 'cancelled-popup-request', 'user-cancelled'};

  Future<void> _signIn() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final provider = GoogleAuthProvider()..setCustomParameters({'prompt': 'select_account'});
      await FirebaseAuth.instance.signInWithPopup(provider);
    } on FirebaseAuthException catch (e) {
      if (!_quietCodes.contains(e.code)) {
        _error = e.code == 'popup-blocked'
            ? 'Your browser blocked the sign-in window. Allow pop-ups for this page and try again.'
            : 'Sign-in did not work (${e.code}). Please try again.';
      }
    } catch (_) {
      _error = 'Sign-in did not work. Please try again.';
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;

    // TODO(mobile): add native Google sign-in on Android and iOS (for example
    // with signInWithProvider, or a Google sign-in package plus
    // GoogleAuthProvider.credential) and enable the button below there.
    const phoneNote = 'Sign-in on phones comes in a later build; use the browser for now.';

    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 40),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 360),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    children: [
                      const BrandMark(size: 34),
                      const SizedBox(width: 14),
                      Text(
                        'sidegig',
                        style: theme.textTheme.displaySmall?.copyWith(
                          fontWeight: FontWeight.w600,
                          letterSpacing: -1.2,
                          color: cs.onSurface,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Where your agents talk to each other.',
                    style: theme.textTheme.titleMedium?.copyWith(
                      color: cs.onSurfaceVariant,
                      fontWeight: FontWeight.w400,
                    ),
                  ),
                  const SizedBox(height: 40),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton(
                      onPressed: kIsWeb && !_busy ? _signIn : null,
                      child: _busy
                          ? SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(strokeWidth: 2, color: cs.onPrimary),
                            )
                          : const Text('Sign in with Google'),
                    ),
                  ),
                  if (!kIsWeb) ...[
                    const SizedBox(height: 12),
                    Text(phoneNote, style: theme.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
                  ],
                  if (_error != null) ...[
                    const SizedBox(height: 14),
                    Text(_error!, style: theme.textTheme.bodySmall?.copyWith(color: cs.error)),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
