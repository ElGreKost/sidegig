// Entry point: starts Firebase from the build-time values, then hands over to
// the auth gate. A build without Firebase values shows a plain notice.

import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';

import 'auth_gate.dart';
import 'config.dart';
import 'screens/status_pages.dart';
import 'theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  if (!AppConfig.hasFirebase) {
    runApp(const SidegigApp(home: NotConfiguredPage()));
    return;
  }
  try {
    await Firebase.initializeApp(options: AppConfig.firebaseOptions);
  } catch (_) {
    runApp(const SidegigApp(
      home: NotConfiguredPage(detail: 'Sign-in could not start with the values in this build.'),
    ));
    return;
  }
  runApp(const SidegigApp(home: AuthGate()));
}

class SidegigApp extends StatelessWidget {
  const SidegigApp({super.key, required this.home});

  final Widget home;

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'sidegig',
        debugShowCheckedModeBanner: false,
        theme: AppTheme.light(),
        darkTheme: AppTheme.dark(),
        themeMode: ThemeMode.system,
        home: home,
      );
}
