// The signed-in shell. Wide screens (900 px and up) show a left rail, a list
// pane and a thread pane side by side; phones get a bottom navigation bar and
// push the thread as its own page.

import 'package:flutter/material.dart';

import '../board.dart';
import '../widgets.dart';
import 'agents.dart';
import 'conversations.dart';
import 'inbox.dart';
import 'new_conversation.dart';
import 'thread.dart';

enum HomeTab { inbox, conversations, agents }

const double _wideBreakpoint = 900;
const double _extendedRailBreakpoint = 1200;

class HomeShell extends StatefulWidget {
  const HomeShell({super.key, required this.board, required this.onSignOut});

  final Board board;
  final VoidCallback onSignOut;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  HomeTab _tab = HomeTab.conversations;
  String? _selectedGigId;
  bool _wide = false;

  Board get board => widget.board;

  void _open(String gigId) {
    if (_wide) {
      setState(() => _selectedGigId = gigId);
    } else {
      Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => ThreadPage(board: board, gigId: gigId),
      ));
    }
  }

  Future<void> _newConversation() async {
    final String? id;
    if (_wide) {
      id = await showDialog<String>(
        context: context,
        builder: (_) => NewConversationScreen(board: board, asDialog: true),
      );
    } else {
      id = await Navigator.of(context).push<String>(MaterialPageRoute(
        fullscreenDialog: true,
        builder: (_) => NewConversationScreen(board: board),
      ));
    }
    if (id != null && mounted) {
      if (_tab == HomeTab.agents) setState(() => _tab = HomeTab.conversations);
      _open(id);
    }
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, constraints) {
      _wide = constraints.maxWidth >= _wideBreakpoint;
      return _wide ? _buildWide(constraints.maxWidth) : _buildNarrow();
    });
  }

  // ------------------------------------------------------------------ panes

  Widget _listPane({required bool wide}) {
    final header = PaneHeader(
      title: switch (_tab) {
        HomeTab.inbox => 'Inbox',
        HomeTab.conversations => 'Conversations',
        HomeTab.agents => 'Agents',
      },
      board: board,
      onSignOut: wide ? null : widget.onSignOut,
      onNew: wide && _tab != HomeTab.agents ? _newConversation : null,
    );
    final selected = wide ? _selectedGigId : null;
    return switch (_tab) {
      HomeTab.inbox => InboxPane(board: board, header: header, selectedId: selected, onOpen: _open),
      HomeTab.conversations =>
        ConversationsPane(board: board, header: header, selectedId: selected, onOpen: _open),
      HomeTab.agents => AgentsPane(board: board, header: header),
    };
  }

  Widget _buildWide(double width) {
    final cs = Theme.of(context).colorScheme;
    final extended = width >= _extendedRailBreakpoint;
    // Wide enough for the five filter chips on one line.
    const listWidth = 380.0;

    final Widget content;
    if (_tab == HomeTab.agents) {
      content = Expanded(child: _listPane(wide: true));
    } else {
      final id = _selectedGigId;
      content = Expanded(
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SizedBox(width: listWidth, child: _listPane(wide: true)),
            VerticalDivider(width: 1, color: cs.outlineVariant),
            Expanded(
              child: id == null
                  ? const EmptyState(
                      icon: Icons.forum_outlined,
                      title: 'Pick a conversation',
                      body: 'Choose one from the list to read it and join in.',
                    )
                  : ThreadView(key: ValueKey(id), board: board, gigId: id),
            ),
          ],
        ),
      );
    }

    return Scaffold(
      body: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _Rail(
            board: board,
            tab: _tab,
            extended: extended,
            onTab: (t) => setState(() => _tab = t),
            onSignOut: widget.onSignOut,
          ),
          VerticalDivider(width: 1, color: cs.outlineVariant),
          content,
        ],
      ),
    );
  }

  Widget _buildNarrow() {
    return ListenableBuilder(
      listenable: board,
      builder: (context, _) {
        final toAnswer = board.inbox.toAnswer.length;
        return Scaffold(
          body: SafeArea(bottom: false, child: _listPane(wide: false)),
          floatingActionButton: _tab == HomeTab.agents
              ? null
              : FloatingActionButton.extended(
                  onPressed: _newConversation,
                  tooltip: 'New conversation',
                  icon: const Icon(Icons.edit_outlined),
                  label: const Text('New'),
                ),
          bottomNavigationBar: NavigationBar(
            selectedIndex: _tab.index,
            onDestinationSelected: (i) => setState(() => _tab = HomeTab.values[i]),
            destinations: [
              NavigationDestination(
                icon: Badge(
                  isLabelVisible: toAnswer > 0,
                  label: Text('$toAnswer'),
                  child: const Icon(Icons.inbox_outlined),
                ),
                selectedIcon: Badge(
                  isLabelVisible: toAnswer > 0,
                  label: Text('$toAnswer'),
                  child: const Icon(Icons.inbox),
                ),
                label: 'Inbox',
              ),
              const NavigationDestination(
                icon: Icon(Icons.forum_outlined),
                selectedIcon: Icon(Icons.forum),
                label: 'Conversations',
              ),
              const NavigationDestination(
                icon: Icon(Icons.smart_toy_outlined),
                selectedIcon: Icon(Icons.smart_toy),
                label: 'Agents',
              ),
            ],
          ),
        );
      },
    );
  }
}

/// Title row of a list pane, with the live state, and on wide screens the
/// New conversation button; on phones the account menu.
class PaneHeader extends StatelessWidget {
  const PaneHeader({super.key, required this.title, required this.board, this.onNew, this.onSignOut});

  final String title;
  final Board board;
  final VoidCallback? onNew;
  final VoidCallback? onSignOut;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 14, 10, 6),
      child: Row(
        children: [
          Expanded(
            child: Row(
              children: [
                Flexible(
                  child: Text(title, style: theme.textTheme.titleLarge, maxLines: 1, overflow: TextOverflow.ellipsis),
                ),
                const SizedBox(width: 10),
                ListenableBuilder(
                  listenable: board,
                  builder: (context, _) => LiveIndicator(board.live, compact: true),
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          if (onNew != null)
            Tooltip(
              message: 'New conversation',
              child: FilledButton.tonalIcon(
                onPressed: onNew,
                icon: const Icon(Icons.edit_outlined, size: 18),
                label: const Text('New'),
                style: FilledButton.styleFrom(minimumSize: const Size(0, 38)),
              ),
            ),
          if (onSignOut != null) _AccountButton(board: board, onSignOut: onSignOut!),
        ],
      ),
    );
  }
}

class _AccountButton extends StatelessWidget {
  const _AccountButton({required this.board, required this.onSignOut});

  final Board board;
  final VoidCallback onSignOut;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final me = board.me;
    final who = me.name.isNotEmpty ? me.name : me.agent;
    return PopupMenuButton<String>(
      tooltip: 'Account',
      position: PopupMenuPosition.under,
      onSelected: (v) {
        if (v == 'out') onSignOut();
      },
      itemBuilder: (_) => [
        PopupMenuItem<String>(
          enabled: false,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(who, style: theme.textTheme.titleSmall),
              if (me.email.isNotEmpty)
                Text(me.email, style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
              Text('Agent: ${me.agent}',
                  style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
            ],
          ),
        ),
        const PopupMenuDivider(),
        const PopupMenuItem<String>(value: 'out', child: Text('Sign out')),
      ],
      child: Padding(
        padding: const EdgeInsets.all(6),
        child: Monogram(who, size: 32, highlight: true),
      ),
    );
  }
}

/// The left rail on wide screens: navigation, then the signed-in person.
class _Rail extends StatelessWidget {
  const _Rail({
    required this.board,
    required this.tab,
    required this.extended,
    required this.onTab,
    required this.onSignOut,
  });

  final Board board;
  final HomeTab tab;
  final bool extended;
  final ValueChanged<HomeTab> onTab;
  final VoidCallback onSignOut;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return ListenableBuilder(
      listenable: board,
      builder: (context, _) {
        final me = board.me;
        final who = me.name.isNotEmpty ? me.name : me.agent;
        final toAnswer = board.inbox.toAnswer.length;
        final items = [
          (HomeTab.inbox, Icons.inbox_outlined, Icons.inbox, 'Inbox', toAnswer),
          (HomeTab.conversations, Icons.forum_outlined, Icons.forum, 'Conversations', 0),
          (HomeTab.agents, Icons.smart_toy_outlined, Icons.smart_toy, 'Agents', 0),
        ];
        return Container(
          width: extended ? 232 : 84,
          color: cs.surfaceContainerLow,
          child: SafeArea(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Padding(
                  padding: EdgeInsets.fromLTRB(extended ? 20 : 0, 20, extended ? 20 : 0, 24),
                  child: Row(
                    mainAxisAlignment: extended ? MainAxisAlignment.start : MainAxisAlignment.center,
                    children: [
                      const BrandMark(size: 24),
                      if (extended) ...[
                        const SizedBox(width: 10),
                        Text('sidegig',
                            style: theme.textTheme.titleMedium?.copyWith(letterSpacing: -0.4)),
                      ],
                    ],
                  ),
                ),
                for (final (t, icon, selIcon, label, count) in items)
                  _RailItem(
                    icon: tab == t ? selIcon : icon,
                    label: label,
                    count: count,
                    selected: tab == t,
                    extended: extended,
                    onTap: () => onTab(t),
                  ),
                const Spacer(),
                Padding(
                  padding: EdgeInsets.symmetric(horizontal: extended ? 20 : 0, vertical: 8),
                  child: Align(
                    alignment: extended ? Alignment.centerLeft : Alignment.center,
                    child: LiveIndicator(board.live, compact: !extended),
                  ),
                ),
                Divider(color: cs.outlineVariant),
                Padding(
                  padding: EdgeInsets.fromLTRB(extended ? 16 : 8, 12, extended ? 12 : 8, 16),
                  child: extended
                      ? Row(
                          children: [
                            Monogram(who, size: 34, highlight: true),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(who,
                                      style: theme.textTheme.titleSmall,
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis),
                                  Text(me.email.isNotEmpty ? me.email : me.agent,
                                      style: theme.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant),
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis),
                                ],
                              ),
                            ),
                            IconButton(
                              tooltip: 'Sign out',
                              onPressed: onSignOut,
                              icon: const Icon(Icons.logout, size: 20),
                            ),
                          ],
                        )
                      : Column(
                          children: [
                            Tooltip(
                              message: me.email.isNotEmpty ? '$who\n${me.email}' : who,
                              child: Monogram(who, size: 34, highlight: true),
                            ),
                            const SizedBox(height: 6),
                            IconButton(
                              tooltip: 'Sign out',
                              onPressed: onSignOut,
                              icon: const Icon(Icons.logout, size: 20),
                            ),
                          ],
                        ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _RailItem extends StatelessWidget {
  const _RailItem({
    required this.icon,
    required this.label,
    required this.count,
    required this.selected,
    required this.extended,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final int count;
  final bool selected;
  final bool extended;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final fg = selected ? cs.onSecondaryContainer : cs.onSurfaceVariant;
    final badge = count > 0
        ? Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
            decoration: BoxDecoration(color: cs.primary, borderRadius: BorderRadius.circular(10)),
            child: Text('$count', style: theme.textTheme.labelSmall?.copyWith(color: cs.onPrimary)),
          )
        : null;

    return Padding(
      padding: EdgeInsets.symmetric(horizontal: extended ? 12 : 10, vertical: 2),
      child: Material(
        color: selected ? cs.secondaryContainer : Colors.transparent,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(12),
          child: Padding(
            padding: EdgeInsets.symmetric(horizontal: extended ? 12 : 4, vertical: extended ? 10 : 8),
            child: extended
                ? Row(
                    children: [
                      Icon(icon, size: 20, color: fg),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Text(label,
                            style: theme.textTheme.labelLarge?.copyWith(
                              color: selected ? cs.onSurface : cs.onSurfaceVariant,
                              fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
                            )),
                      ),
                      if (badge != null) badge,
                    ],
                  )
                : Column(
                    children: [
                      Badge(
                        isLabelVisible: count > 0,
                        label: Text('$count'),
                        child: Icon(icon, size: 22, color: fg),
                      ),
                      const SizedBox(height: 4),
                      FittedBox(
                        fit: BoxFit.scaleDown,
                        child: Text(label, maxLines: 1, style: theme.textTheme.labelSmall?.copyWith(color: fg)),
                      ),
                    ],
                  ),
          ),
        ),
      ),
    );
  }
}
