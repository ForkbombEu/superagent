window.__ModuleLoader__.load({
  id: 'superagent-console',
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    var React = require('react');

    var PANEL_ID = 'superagent-console';
    var NS = 'superagentConsole';

    function ConsoleIcon(props) {
      var size = (props && props.size) || 20;
      return React.createElement(
        'svg',
        {
          width: size,
          height: size,
          viewBox: '0 0 24 24',
          fill: 'none',
          stroke: 'currentColor',
          strokeWidth: 1.8,
          strokeLinecap: 'round',
          strokeLinejoin: 'round',
          'aria-hidden': true,
        },
        React.createElement('path', {
          d: 'M4 6.5h16v11H4z',
        }),
        React.createElement('path', {
          d: 'M8 10h3M8 13h8',
        }),
      );
    }

    function ConsolePanel() {
      return React.createElement('iframe', {
        src: '/console/',
        title: 'Console',
        style: {
          border: 0,
          width: '100%',
          height: '100%',
          display: 'block',
          background: '#f7f4ef',
        },
      });
    }

    var inject = ['slots'];

    function apply(ctx) {
      ctx.slots.inject('main', function () {
        return ctx.slots.register(
          { name: 'main', key: PANEL_ID, locale: NS, inject: function () { return {}; } },
          ConsolePanel,
        );
      });
      ctx.slots.inject('sidebar.panellist', function () {
        return ctx.slots.register(
          {
            name: 'sidebar.panellist',
            id: PANEL_ID,
            order: 20,
            label: function () { return 'Console'; },
            locale: NS,
          },
          ConsoleIcon,
        );
      });
    }

    exports.inject = inject;
    exports.apply = apply;
    return module.exports;
  },
});
