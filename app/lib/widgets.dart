// Small shared widgets and formatting helpers used across the screens.

import 'package:flutter/material.dart';

import 'api.dart';
import 'board.dart';
import 'models.dart';
import 'theme.dart';

const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

String _two(int n) => n.toString().padLeft(2, '0');

String _date(DateTime t) {
  final now = DateTime.now();
  final d = '${t.day} ${_months[t.month - 1]}';
  return t.year == now.year ? d : '$d ${t.year}';
}

/// "just now", "5 min ago", "3 h ago", "yesterday", "4 d ago", then a date.
String relativeTime(DateTime? t) {
  if (t == null) return '';
  final now = DateTime.now();
  final diff = now.difference(t);
  if (diff.inSeconds < 60) return 'just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes} min ago';
  if (diff.inHours < 24) return '${diff.inHours} h ago';
  final today = DateTime(now.year, now.month, now.day);
  final day = DateTime(t.year, t.month, t.day);
  final days = today.difference(day).inDays;
  if (days == 1) return 'yesterday';
  if (days < 7) return '$days d ago';
  return _date(t);
}

/// "14:57" for today, "22 Nov, 14:57" otherwise. Falls back to [raw].
String clockTime(DateTime? t, String raw) {
  if (t == null) return raw;
  final now = DateTime.now();
  final hm = '${_two(t.hour)}:${_two(t.minute)}';
  if (t.year == now.year && t.month == now.month && t.day == now.day) return hm;
  return '${_date(t)}, $hm';
}

/// Shows an API error (error line plus hint) in a SnackBar.
void showError(BuildContext context, Object error) {
  final text = error is ApiException ? error.message : 'Something went wrong. Please try again.';
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(text)));
}

void showNote(BuildContext context, String text) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(text)));
}

/// The product mark: two offset rounded squares, two agents in conversation.
class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.size = 28});

  final double size;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final box = size * 0.68;
    return SizedBox(
      width: size,
      height: size,
      child: Stack(
        children: [
          Positioned(
            right: 0,
            bottom: 0,
            child: Container(
              width: box,
              height: box,
              decoration: BoxDecoration(
                color: cs.primary.withValues(alpha: 0.35),
                borderRadius: BorderRadius.circular(size * 0.2),
              ),
            ),
          ),
          Positioned(
            left: 0,
            top: 0,
            child: Container(
              width: box,
              height: box,
              decoration: BoxDecoration(
                color: cs.primary,
                borderRadius: BorderRadius.circular(size * 0.2),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class StatusChip extends StatelessWidget {
  const StatusChip(this.status, {super.key});

  final String status;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final (bg, fg) = AppTheme.statusColors(status, theme.colorScheme);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(6)),
      child: Text(
        status,
        style: theme.textTheme.labelSmall?.copyWith(color: fg, fontWeight: FontWeight.w600, letterSpacing: 0.2),
      ),
    );
  }
}

/// "asker → to_agent".
class RouteLine extends StatelessWidget {
  const RouteLine({super.key, required this.from, required this.to, this.style});

  final String from;
  final String to;
  final TextStyle? style;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final s = style ?? theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant);
    return Text(
      '${from.isEmpty ? '?' : from} → ${to.isEmpty ? 'anyone' : to}',
      style: s,
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
    );
  }
}

/// A monogram circle for an agent or a person.
class Monogram extends StatelessWidget {
  const Monogram(this.id, {super.key, this.size = 32, this.highlight = false});

  final String id;
  final double size;
  final bool highlight;

  static String initials(String id) {
    final parts = id.split(RegExp(r'[^A-Za-z0-9]+')).where((p) => p.isNotEmpty).toList();
    if (parts.isEmpty) return '?';
    if (parts.length == 1) return parts.first.substring(0, 1).toUpperCase();
    return (parts[0].substring(0, 1) + parts[1].substring(0, 1)).toUpperCase();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: highlight ? cs.primaryContainer : cs.surfaceContainerHigh,
      ),
      child: Text(
        initials(id),
        style: TextStyle(
          fontSize: size * 0.36,
          fontWeight: FontWeight.w600,
          color: highlight ? cs.onPrimaryContainer : cs.onSurfaceVariant,
        ),
      ),
    );
  }
}

/// One gig in a list.
class GigTile extends StatelessWidget {
  const GigTile({super.key, required this.gig, required this.onTap, this.selected = false, this.mine = false});

  final Gig gig;
  final VoidCallback onTap;
  final bool selected;
  final bool mine;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return Material(
      color: selected ? cs.secondaryContainer.withValues(alpha: 0.7) : Colors.transparent,
      borderRadius: BorderRadius.circular(12),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(
                      gig.title,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.titleSmall?.copyWith(height: 1.3),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Text(
                    relativeTime(gig.updatedAt),
                    style: theme.textTheme.labelSmall?.copyWith(color: cs.onSurfaceVariant),
                  ),
                ],
              ),
              const SizedBox(height: 6),
              Row(
                children: [
                  if (mine) ...[
                    Container(
                      width: 6,
                      height: 6,
                      decoration: BoxDecoration(color: cs.primary, shape: BoxShape.circle),
                    ),
                    const SizedBox(width: 6),
                  ],
                  Expanded(child: RouteLine(from: gig.asker, to: gig.toAgent)),
                  const SizedBox(width: 8),
                  StatusChip(gig.status),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class SectionHeader extends StatelessWidget {
  const SectionHeader(this.title, {super.key, this.count, this.subtitle});

  final String title;
  final int? count;
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(title.toUpperCase(),
                  style: theme.textTheme.labelMedium?.copyWith(
                    color: cs.onSurfaceVariant,
                    letterSpacing: 0.8,
                    fontWeight: FontWeight.w600,
                  )),
              if (count != null) ...[
                const SizedBox(width: 8),
                Text('$count', style: theme.textTheme.labelMedium?.copyWith(color: cs.outline)),
              ],
            ],
          ),
          if (subtitle != null) ...[
            const SizedBox(height: 2),
            Text(subtitle!, style: theme.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
          ],
        ],
      ),
    );
  }
}

class EmptyState extends StatelessWidget {
  const EmptyState({super.key, required this.icon, required this.title, this.body, this.action});

  final IconData icon;
  final String title;
  final String? body;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 320),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 28, color: cs.outline),
              const SizedBox(height: 12),
              Text(title, textAlign: TextAlign.center, style: theme.textTheme.titleSmall),
              if (body != null) ...[
                const SizedBox(height: 6),
                Text(body!,
                    textAlign: TextAlign.center,
                    style: theme.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
              ],
              if (action != null) ...[const SizedBox(height: 16), action!],
            ],
          ),
        ),
      ),
    );
  }
}

/// A small dot plus label for the live feed state.
class LiveIndicator extends StatelessWidget {
  const LiveIndicator(this.state, {super.key, this.compact = false});

  final LiveState state;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final (color, label) = switch (state) {
      LiveState.live => (cs.primary, 'Live'),
      LiveState.connecting => (cs.outline, 'Connecting'),
      LiveState.retrying => (const Color(0xFFC2410C), 'Reconnecting'),
    };
    final dot = Container(width: 8, height: 8, decoration: BoxDecoration(color: color, shape: BoxShape.circle));
    if (compact) return Tooltip(message: label, child: dot);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        dot,
        const SizedBox(width: 6),
        Text(label, style: theme.textTheme.labelSmall?.copyWith(color: cs.onSurfaceVariant)),
      ],
    );
  }
}

/// Lays a widget out at a readable width in the middle of wide screens.
class Readable extends StatelessWidget {
  const Readable({super.key, required this.child, this.maxWidth = 720});

  final Widget child;
  final double maxWidth;

  @override
  Widget build(BuildContext context) => Align(
        alignment: Alignment.topCenter,
        child: ConstrainedBox(
          constraints: BoxConstraints(maxWidth: maxWidth),
          child: SizedBox(width: double.infinity, child: child),
        ),
      );
}
