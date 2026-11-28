// Simple full-screen notices: loading, missing configuration, no access and
// connection errors.

import 'package:flutter/material.dart';

import '../widgets.dart';

/// A centred column with the product mark on top.
class _Notice extends StatelessWidget {
  const _Notice({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Scaffold(
        body: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 380),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [const BrandMark(size: 32), const SizedBox(height: 24), ...children],
                ),
              ),
            ),
          ),
        ),
      );
}

class SplashPage extends StatelessWidget {
  const SplashPage({super.key});

  @override
  Widget build(BuildContext context) => const Scaffold(
        body: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              BrandMark(size: 32),
              SizedBox(height: 24),
              SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)),
            ],
          ),
        ),
      );
}

class NotConfiguredPage extends StatelessWidget {
  const NotConfiguredPage({super.key, this.detail});

  final String? detail;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return _Notice(children: [
      Text('This build has no sign-in configured', style: theme.textTheme.titleLarge),
      const SizedBox(height: 10),
      Text(
        detail ?? 'Build the app with the Firebase values listed in its README.',
        style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
      ),
    ]);
  }
}

class NoAccessPage extends StatelessWidget {
  const NoAccessPage({super.key, required this.email, required this.onSignOut});

  final String email;
  final VoidCallback onSignOut;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return _Notice(children: [
      Text('This account has no access to this board.', style: theme.textTheme.titleLarge),
      const SizedBox(height: 10),
      if (email.isNotEmpty)
        SelectableText(
          email,
          style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
        ),
      const SizedBox(height: 24),
      OutlinedButton(onPressed: onSignOut, child: const Text('Sign out')),
    ]);
  }
}

class ErrorPage extends StatelessWidget {
  const ErrorPage({super.key, required this.message, required this.onRetry, required this.onSignOut});

  final String message;
  final VoidCallback onRetry;
  final VoidCallback onSignOut;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return _Notice(children: [
      Text('Could not open the board', style: theme.textTheme.titleLarge),
      const SizedBox(height: 10),
      Text(message, style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
      const SizedBox(height: 24),
      Row(children: [
        FilledButton(onPressed: onRetry, child: const Text('Try again')),
        const SizedBox(width: 12),
        TextButton(onPressed: onSignOut, child: const Text('Sign out')),
      ]),
    ]);
  }
}
