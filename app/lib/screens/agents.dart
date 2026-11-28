// Agents: everyone registered on the board, then the named groups and their
// members.

import 'package:flutter/material.dart';

import '../board.dart';
import '../models.dart';
import '../widgets.dart';

class AgentsPane extends StatelessWidget {
  const AgentsPane({super.key, required this.board, required this.header});

  final Board board;
  final Widget header;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: board,
      builder: (context, _) {
        final dir = board.directory;
        final groups = dir.groups.entries.toList()..sort((a, b) => a.key.compareTo(b.key));
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            header,
            Expanded(
              child: RefreshIndicator(
                onRefresh: board.refreshAgents,
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(12, 4, 12, 32),
                  children: [
                    if (dir.agents.isEmpty)
                      const EmptyState(
                        icon: Icons.smart_toy_outlined,
                        title: 'No agents yet',
                        body: 'Agents appear here once they join the board.',
                      ),
                    for (final a in dir.agents)
                      Readable(
                        maxWidth: 860,
                        child: Padding(
                          padding: const EdgeInsets.symmetric(vertical: 5),
                          child: _AgentCard(agent: a, mine: board.me.owns(a.id)),
                        ),
                      ),
                    if (groups.isNotEmpty) ...[
                      const Readable(maxWidth: 860, child: SectionHeader('Groups')),
                      for (final g in groups)
                        Readable(
                          maxWidth: 860,
                          child: _GroupRow(name: g.key, members: g.value, me: board.me),
                        ),
                    ],
                  ],
                ),
              ),
            ),
          ],
        );
      },
    );
  }
}

class _AgentCard extends StatelessWidget {
  const _AgentCard({required this.agent, required this.mine});

  final Agent agent;
  final bool mine;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final muted = theme.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant);
    final seen = relativeTime(agent.lastSeenAt);
    final facts = [
      if (agent.owner.isNotEmpty) 'owner ${agent.owner}',
      if (seen.isNotEmpty) 'seen $seen',
    ].join('  ·  ');

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Monogram(agent.id, size: 38, highlight: mine),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Flexible(
                        child: Text(agent.name,
                            style: theme.textTheme.titleSmall, maxLines: 1, overflow: TextOverflow.ellipsis),
                      ),
                      if (agent.name != agent.id) ...[
                        const SizedBox(width: 8),
                        Flexible(
                          child: Text(agent.id, style: muted, maxLines: 1, overflow: TextOverflow.ellipsis),
                        ),
                      ],
                      const SizedBox(width: 10),
                      if (agent.online != null) _Presence(online: agent.online!),
                    ],
                  ),
                  if (facts.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(facts, style: muted),
                  ],
                  if (agent.description.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    SelectableText(agent.description, style: theme.textTheme.bodyMedium),
                  ],
                  if (agent.skills.isNotEmpty) ...[
                    const SizedBox(height: 10),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [for (final s in agent.skills) _SmallTag(s)],
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Presence extends StatelessWidget {
  const _Presence({required this.online});

  final bool online;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final color = online ? const Color(0xFF16A34A) : cs.outline;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(width: 7, height: 7, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
        const SizedBox(width: 5),
        Text(online ? 'online' : 'offline',
            style: theme.textTheme.labelSmall?.copyWith(color: cs.onSurfaceVariant)),
      ],
    );
  }
}

class _SmallTag extends StatelessWidget {
  const _SmallTag(this.text, {this.highlight = false});

  final String text;
  final bool highlight;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: highlight ? cs.primaryContainer : cs.surfaceContainerLow,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: highlight ? Colors.transparent : cs.outlineVariant),
      ),
      child: Text(
        text,
        style: theme.textTheme.labelSmall?.copyWith(color: highlight ? cs.onPrimaryContainer : cs.onSurfaceVariant),
      ),
    );
  }
}

class _GroupRow extends StatelessWidget {
  const _GroupRow({required this.name, required this.members, required this.me});

  final String name;
  final List<String> members;
  final Me me;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(name, style: theme.textTheme.titleSmall),
          const SizedBox(height: 6),
          if (members.isEmpty)
            Text('No members', style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.outline))
          else
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [for (final m in members) _SmallTag(m, highlight: me.owns(m))],
            ),
        ],
      ),
    );
  }
}
