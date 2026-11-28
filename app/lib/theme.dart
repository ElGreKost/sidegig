// Look and feel: one deep teal accent on neutral greys, light and dark.

import 'package:flutter/material.dart';

class AppTheme {
  AppTheme._();

  static const Color accent = Color(0xFF0F766E);

  static ThemeData light() => _build(_lightScheme());
  static ThemeData dark() => _build(_darkScheme());

  static ColorScheme _lightScheme() => ColorScheme.fromSeed(seedColor: accent).copyWith(
        primary: accent,
        onPrimary: Colors.white,
        primaryContainer: const Color(0xFFD3F2EE),
        onPrimaryContainer: const Color(0xFF0B4A45),
        secondary: const Color(0xFF52606D),
        onSecondary: Colors.white,
        secondaryContainer: const Color(0xFFDDEFEC),
        onSecondaryContainer: const Color(0xFF0B4A45),
        surface: const Color(0xFFFAFAF9),
        onSurface: const Color(0xFF1A1B1E),
        onSurfaceVariant: const Color(0xFF5C5F66),
        surfaceContainerLowest: Colors.white,
        surfaceContainerLow: const Color(0xFFF4F4F3),
        surfaceContainer: const Color(0xFFEFEFEE),
        surfaceContainerHigh: const Color(0xFFE9E9E8),
        surfaceContainerHighest: const Color(0xFFE3E3E2),
        outline: const Color(0xFF9A9CA3),
        outlineVariant: const Color(0xFFE2E2E4),
        surfaceTint: Colors.transparent,
      );

  static ColorScheme _darkScheme() =>
      ColorScheme.fromSeed(seedColor: accent, brightness: Brightness.dark).copyWith(
        primary: const Color(0xFF5CC6B8),
        onPrimary: const Color(0xFF00201D),
        primaryContainer: const Color(0xFF134E4A),
        onPrimaryContainer: const Color(0xFFCDEFEA),
        secondary: const Color(0xFFA9B4BF),
        onSecondary: const Color(0xFF1A1B1E),
        secondaryContainer: const Color(0xFF1E3835),
        onSecondaryContainer: const Color(0xFFCDEFEA),
        surface: const Color(0xFF121314),
        onSurface: const Color(0xFFE6E6E8),
        onSurfaceVariant: const Color(0xFFA2A4AB),
        surfaceContainerLowest: const Color(0xFF0D0E0F),
        surfaceContainerLow: const Color(0xFF17181A),
        surfaceContainer: const Color(0xFF1C1D1F),
        surfaceContainerHigh: const Color(0xFF232427),
        surfaceContainerHighest: const Color(0xFF2B2C2F),
        outline: const Color(0xFF5E6067),
        outlineVariant: const Color(0xFF2D2E32),
        surfaceTint: Colors.transparent,
      );

  static ThemeData _build(ColorScheme cs) {
    final base = ThemeData(colorScheme: cs, useMaterial3: true);
    final text = base.textTheme.copyWith(
      headlineMedium: base.textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w600, letterSpacing: -0.5),
      titleLarge: base.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w600, letterSpacing: -0.2),
      titleMedium: base.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w600),
      titleSmall: base.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w600),
      bodyLarge: base.textTheme.bodyLarge?.copyWith(height: 1.45),
      bodyMedium: base.textTheme.bodyMedium?.copyWith(height: 1.45),
    );
    final rounded12 = RoundedRectangleBorder(borderRadius: BorderRadius.circular(12));

    return base.copyWith(
      scaffoldBackgroundColor: cs.surface,
      textTheme: text,
      appBarTheme: AppBarTheme(
        backgroundColor: cs.surface,
        foregroundColor: cs.onSurface,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        margin: EdgeInsets.zero,
        color: cs.surfaceContainerLowest,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: BorderSide(color: cs.outlineVariant),
        ),
      ),
      dividerTheme: DividerThemeData(color: cs.outlineVariant, thickness: 1, space: 1),
      inputDecorationTheme: InputDecorationThemeData(
        filled: true,
        fillColor: cs.surfaceContainerLow,
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: cs.outlineVariant),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: cs.primary, width: 1.5),
        ),
        disabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: cs.outlineVariant.withValues(alpha: 0.5)),
        ),
      ),
      chipTheme: ChipThemeData(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        side: BorderSide(color: cs.outlineVariant),
        padding: const EdgeInsets.symmetric(horizontal: 4),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(minimumSize: const Size(0, 44), shape: rounded12),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(0, 44),
          shape: rounded12,
          side: BorderSide(color: cs.outlineVariant),
        ),
      ),
      textButtonTheme: TextButtonThemeData(style: TextButton.styleFrom(shape: rounded12)),
      floatingActionButtonTheme: FloatingActionButtonThemeData(
        backgroundColor: cs.primary,
        foregroundColor: cs.onPrimary,
        elevation: 1,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: cs.surfaceContainerLow,
        indicatorColor: cs.secondaryContainer,
        height: 66,
        elevation: 0,
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        shape: rounded12,
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: cs.surfaceContainerLowest,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      ),
      listTileTheme: ListTileThemeData(shape: rounded12),
      tooltipTheme: TooltipThemeData(waitDuration: const Duration(milliseconds: 500)),
    );
  }

  /// Background and text colour for a gig status chip.
  static (Color, Color) statusColors(String status, ColorScheme cs) {
    final dark = cs.brightness == Brightness.dark;
    switch (status) {
      case 'open':
        return (cs.primaryContainer, cs.onPrimaryContainer);
      case 'claimed':
        return dark
            ? (const Color(0xFF3A3220), const Color(0xFFF1D9A6))
            : (const Color(0xFFFBF0D9), const Color(0xFF6B4E10));
      case 'answered':
        return dark
            ? (const Color(0xFF242C42), const Color(0xFFC9D4F5))
            : (const Color(0xFFE4E9F8), const Color(0xFF2B3A67));
      case 'stale':
        return dark
            ? (const Color(0xFF3D2622), const Color(0xFFF2C4B9))
            : (const Color(0xFFFBE6E1), const Color(0xFF7A2E1E));
      default:
        return (cs.surfaceContainerHigh, cs.onSurfaceVariant);
    }
  }
}
